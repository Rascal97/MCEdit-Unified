import itertools
import time
from level import FakeChunk, MCLevel
import logging
from materials import pocketMaterials

import os

import leveldb_mcpe
from mclevelbase import ChunkNotPresent, ChunkMalformed
import nbt
import numpy
import struct
from infiniteworld import ChunkedLevelMixin, SessionLockLost
from level import LightedChunk
from contextlib import contextmanager
from pymclevel import entity

logger = logging.getLogger(__name__)

"""
Add player support.
Add a way of creating new levels
Add a way to test for broken world and repair them (use leveldbs repairer, it's been wrapped already)
Setup loggers
"""


# noinspection PyUnresolvedReferences
@contextmanager
def littleEndianNBT():
    """
    Pocket edition NBT files are encoded in little endian, instead of big endian.
    This sets all the required paramaters to read little endian NBT, and makes sure they get set back after usage.
    :return: None
    """

    # We need to override the function to access the hard-coded endianness.
    def override_write_string(string, buf):
        encoded = string.encode('utf-8')
        buf.write(struct.pack("<h%ds" % (len(encoded),), len(encoded), encoded))

    def reset_write_string(string, buf):
        encoded = string.encode('utf-8')
        buf.write(struct.pack(">h%ds" % (len(encoded),), len(encoded), encoded))

    def override_byte_array_write_value(self, buf):
        value_str = self.value.tostring()
        buf.write(struct.pack("<I%ds" % (len(value_str),), self.value.size, value_str))

    def reset_byte_array_write_value(self, buf):
        value_str = self.value.tostring()
        buf.write(struct.pack("<I%ds" % (len(value_str),), self.value.size, value_str))

    nbt.string_len_fmt = struct.Struct("<H")
    nbt.TAG_Byte.fmt = struct.Struct("<b")
    nbt.TAG_Short.fmt = struct.Struct("<h")
    nbt.TAG_Int.fmt = struct.Struct("<i")
    nbt.TAG_Long.fmt = struct.Struct("<q")
    nbt.TAG_Float.fmt = struct.Struct("<f")
    nbt.TAG_Double.fmt = struct.Struct("<d")
    nbt.TAG_Int_Array.dtype = numpy.dtype("<u4")
    nbt.TAG_Short_Array.dtype = numpy.dtype("<u2")
    nbt.write_string = override_write_string
    nbt.TAG_Byte_Array.write_value = override_byte_array_write_value
    yield
    nbt.string_len_fmt = struct.Struct(">H")
    nbt.TAG_Byte.fmt = struct.Struct(">b")
    nbt.TAG_Short.fmt = struct.Struct(">h")
    nbt.TAG_Int.fmt = struct.Struct(">i")
    nbt.TAG_Long.fmt = struct.Struct(">q")
    nbt.TAG_Float.fmt = struct.Struct(">f")
    nbt.TAG_Double.fmt = struct.Struct(">d")
    nbt.TAG_Int_Array.dtype = numpy.dtype(">u4")
    nbt.TAG_Short_Array.dtype = numpy.dtype(">u2")
    nbt.write_string = reset_write_string
    nbt.TAG_Byte_Array.write_value = reset_byte_array_write_value


def loadNBTCompoundList(data, littleEndian=True):
    """
    Loads a list of NBT Compound tags from a bunch of data.
    Uses sep to determine where the next Compound tag starts.
    :param data: str, the NBT to load from
    :param littleEndian: bool. Determines endianness
    :return: list of TAG_Compounds
    """

    def load(_data):
        sep = "\x00\x00\x00\x00\n"
        sep_data = _data.split(sep)
        compounds = []
        for d in sep_data:
            if len(d) != 0:
                if not d.startswith("\n"):
                    d = "\n" + d
                tag = (nbt.load(buf=(d + '\x00\x00\x00\x00')))
                compounds.append(tag)
        return compounds

    if littleEndian:
        with littleEndianNBT():
            return load(data)
    else:
        return load(data)


def TagProperty(tagName, tagType, default_or_func=None):
    """
    Copied from infiniteworld.py. Custom property object to handle NBT-tag properties.
    :param tagName: str, Name of the NBT-tag
    :param tagType: int, (nbt.TAG_TYPE) Type of the NBT-tag
    :param default_or_func: function or default value. If function, function should return the default.
    :return: property
    """
    def getter(self):
        if tagName not in self.root_tag:
            if hasattr(default_or_func, "__call__"):
                default = default_or_func(self)
            else:
                default = default_or_func

            self.root_tag[tagName] = tagType(default)
        return self.root_tag[tagName].value

    def setter(self, val):
        self.root_tag[tagName] = tagType(value=val)

    return property(getter, setter)


class PocketLeveldbDatabase(object):
    """
    Not to be confused with leveldb_mcpe.DB
    A PocketLeveldbDatabase is an interface around leveldb_mcpe.DB, providing various functions
    to load/write chunk data, and access the level.dat file.
    The leveldb_mcpe.DB object handles the actual leveldb database.
    To access the actual database, world_db() should be called.
    """
    holdDatabaseOpen = True
    _world_db = None

    @contextmanager
    def world_db(self):
        """
        Opens a leveldb and keeps it open until editing finished.
        :yield: DB
        """
        if PocketLeveldbDatabase.holdDatabaseOpen:
            if self._world_db is None:
                self._world_db = leveldb_mcpe.DB(self.options, os.path.join(str(self.path), 'db'))
            yield self._world_db
            pass
        else:
            db = leveldb_mcpe.DB(self.options, os.path.join(str(self.path), 'db'))
            yield db
            del db

    def __init__(self, path):
        """
        :param path: string, path to file
        :return: None
        """
        self.path = path

        if not os.path.exists(path):
            file(path, 'w').close()

        self.options = leveldb_mcpe.Options()
        self.writeOptions = leveldb_mcpe.WriteOptions()
        self.readOptions = leveldb_mcpe.ReadOptions()

        needsRepair = False
        with self.world_db() as db:
            pass  # Setup tests to see if the world is broken

        if needsRepair:
            leveldb_mcpe.RepairWrapper(os.path.join(path, 'db'))
            # Maybe setup a logger message with number of chunks in the database etc.

    def close(self):
        """
        Should be called before deleting this instance of the level.
        Not calling this method may result in corrupted worlds
        :return: None
        """
        if PocketLeveldbDatabase.holdDatabaseOpen:
            if self._world_db is not None:
                del self._world_db
                self._world_db = None

    def _readChunk(self, cx, cz, readOptions=None):
        """
        :param cx, cz: int Coordinates of the chunk
        :param readOptions: ReadOptions
        :return: None
        """
        key = struct.pack('<i', cx) + struct.pack('<i', cz)
        with self.world_db() as db:
            rop = self.readOptions if readOptions is None else readOptions

            # Only way to see if value exists is by failing db.Get()
            try:
                terrain = db.Get(rop, key + "0")
            except RuntimeError:
                return None

            try:
                tile_entities = db.Get(rop, key + "1")
            except RuntimeError:
                tile_entities = None

            try:
                entities = db.Get(rop, key + "2")
            except RuntimeError:
                entities = None

        if len(terrain) != 83200:
            raise ChunkMalformed(str(len(terrain)))

        logger.debug("CHUNK LOAD %s %s", cx, cz)
        return terrain, tile_entities, entities

    def saveChunk(self, chunk, batch=None, writeOptions=None):
        """
        :param chunk: PocketLeveldbChunk
        :param batch: WriteBatch
        :param writeOptions: WriteOptions
        :return: None
        """
        cx, cz = chunk.chunkPosition
        data = chunk.savedData()
        key = struct.pack('<i', cx) + struct.pack('<i', cz)

        if batch is None:
            with self.world_db() as db:
                wop = self.writeOptions if writeOptions is None else writeOptions
                db.Put(key + "0", data[0], wop)
                if data[1] is not None:
                    db.Put(key + "1", data[1], wop)
                if data[2] is not None:
                    db.Put(key + "2", data[2], wop)
        else:
            batch.Put(key + "0", data[0])
            if data[1] is not None:
                batch.Put(key + "1", data[1])
            if data[2] is not None:
                batch.Put(key + "2", data[2])

    def loadChunk(self, cx, cz, world):
        """
        :param cx, cz: int Coordinates of the chunk
        :param world: PocketLeveldbWorld
        :return: PocketLeveldbChunk
        """
        data = self._readChunk(cx, cz)
        if data is None:
            raise ChunkNotPresent((cx, cz, self))

        chunk = PocketLeveldbChunk(cx, cz, data, world)
        return chunk

    _allChunks = None

    def deleteChunk(self, cx, cz, batch=None, writeOptions=None):
        if batch is None:
            with self.world_db() as db:
                key = struct.pack('<i', cx) + struct.pack('<i', cz) + "0"
                wop = self.writeOptions if writeOptions is None else writeOptions
                db.Delete(wop, key)
        else:
            key = struct.pack('<i', cx) + struct.pack('<i', cz) + "0"
            batch.Delete(key)

        logger.debug("DELETED CHUNK %s %s", cx, cz)

    def getAllChunks(self, readOptions=None):
        """
        Returns a list of all chunks that have terrain data in the database.
        Chunks with only Entities or TileEntities are ignored.
        :param readOptions: ReadOptions
        :return: list
        """
        with self.world_db() as db:
            allChunks = []
            rop = self.readOptions if readOptions is None else readOptions

            it = db.NewIterator(rop)
            it.SeekToFirst()
            while it.Valid():
                key = it.key()
                raw_x = key[0:4]
                raw_z = key[4:8]
                t = key[8]

                if t == "0":
                    cx, cz = struct.unpack('<i', raw_x), struct.unpack('<i', raw_z)
                    allChunks.append((cx[0], cz[0]))
                it.Next()
            it.status()  # All this does is cause an exception if something went wrong. Might be unneeded?
            del it
            return allChunks


class InvalidPocketLevelDBWorldException(Exception):
    pass


class PocketLeveldbWorld(ChunkedLevelMixin, MCLevel):
    Height = 128
    Width = 0
    Length = 0

    isInfinite = True
    materials = pocketMaterials
    # root_tag = None

    _allChunks = None  # An array of cx, cz pairs.
    _loadedChunks = {}  # A dictionary of actual PocketLeveldbChunk objects mapped by (cx, cz)

    @property
    def allChunks(self):
        """
        :return: list with all chunks in the world.
        """
        if self._allChunks is None:
            self._allChunks = self.worldFile.getAllChunks()
        return self._allChunks

    def __init__(self, filename=None, create=False, random_seed=None, last_played=None, readonly=False):
        """
        :param filename: path to the root dir of the level
        :return:
        """
        if not os.path.isdir(filename):
            filename = os.path.dirname(filename)
        self.filename = filename

        self.worldFile = PocketLeveldbDatabase(os.path.join(filename))
        self.loadLevelDat(create, random_seed, last_played)

    def loadLevelDat(self, create=False, random_seed=None, last_played=None):
        with littleEndianNBT():
            if create:
                return  # TODO implement this with a _create()
            root_tag_buf = open(os.path.join(self.filename, 'level.dat')).read()
            magic, length, root_tag_buf = root_tag_buf[:4], root_tag_buf[4:8], root_tag_buf[8:]
            try:
                if nbt.TAG_Int.fmt.unpack(magic)[0] < 3:
                    logger.info("Found an old level.dat file. Aborting world load")
                    raise InvalidPocketLevelDBWorldException()  # TODO Maybe try convert/load old PE world?
                if len(root_tag_buf) != nbt.TAG_Int.fmt.unpack(length)[0]:
                    raise nbt.NBTFormatError()
                self.root_tag = nbt.load(buf=root_tag_buf)
            except nbt.NBTFormatError, e:
                logger.info("Failed to load level.dat, trying to load level.dat_old ({0})".format(e))

    # --- NBT Tag variables ---

    SizeOnDisk = TagProperty('SizeOnDisk', nbt.TAG_Int, 0)
    RandomSeed = TagProperty('RandomSeed', nbt.TAG_Int, 0)

    # TODO PE worlds have a different day length, this has to be changed to that.
    Time = TagProperty('Time', nbt.TAG_Long, 0)
    LastPlayed = TagProperty('LastPlayed', nbt.TAG_Long, lambda self: long(time.time() * 1000))

    LevelName = TagProperty('LevelName', nbt.TAG_String, lambda self: self.defaultDisplayName)
    GeneratorName = TagProperty('Generator', nbt.TAG_String, 'Infinite')

    GameType = TagProperty('GameType', nbt.TAG_Int, 0)

    def defaultDisplayName(self):
        return os.path.basename(os.path.dirname(self.filename))

    def getChunk(self, cx, cz):
        """
        Used to obtain a chunk from the database.
        :param cx, cz: cx, cz coordinates of the chunk
        :return: PocketLeveldbChunk
        """
        c = self._loadedChunks.get((cx, cz))
        if c is None:
            c = self.worldFile.loadChunk(cx, cz, self)
            self._loadedChunks[(cx, cz)] = c
        return c

    def unload(self):
        """
        Unload all chunks and close all open file-handlers.
        """
        self._loadedChunks.clear()
        self._allChunks = None
        self.worldFile.close()

    def close(self):
        """
        Unload all chunks and close all open file-handlers. Discard any unsaved data.
        """
        self.unload()
        try:
            pass  # Setup a way to close a work-folder?
        except SessionLockLost:
            pass

    def deleteChunk(self, cx, cz, batch=None):
        """
        Deletes a chunk at given cx, cz. Deletes using the batch if batch is given, uses world_db() otherwise.
        :param cx, cz Coordinates of the chunk
        :param batch WriteBatch
        :return: None
        """
        self.worldFile.deleteChunk(cx, cz, batch=batch)
        if self._loadedChunks is not None and (cx, cz) in self._loadedChunks:  # Unnecessary check?
            del self._loadedChunks[(cx, cz)]
            self.allChunks.remove((cx, cz))

    def deleteChunksInBox(self, box):
        """
        Deletes all chunks in a given box.
        :param box pymclevel.box.BoundingBox
        :return: None
        """
        logger.info(u"Deleting {0} chunks in {1}".format((box.maxcx - box.mincx) * (box.maxcz - box.mincz),
                                                         ((box.mincx, box.mincz), (box.maxcx, box.maxcz))))
        i = 0
        ret = []
        batch = leveldb_mcpe.WriteBatch()
        for cx, cz in itertools.product(xrange(box.mincx, box.maxcx), xrange(box.mincz, box.maxcz)):
            i += 1
            if self.containsChunk(cx, cz):
                self.deleteChunk(cx, cz, batch=batch)
                ret.append((cx, cz))

            assert not self.containsChunk(cx, cz), "Just deleted {0} but it didn't take".format((cx, cz))

            if i % 100 == 0:
                logger.info(u"Chunk {0}...".format(i))

        with self.worldFile.world_db() as db:
            wop = self.worldFile.writeOptions
            db.Write(wop, batch)

        del batch
        return ret

    @classmethod
    def _isLevel(cls, filename):
        """
        Determines whether or not the path in filename has a Pocket Edition 0.9.0 or later in it
        :param filename string with path to level root directory.
        """
        clp = ("db", "level.dat")

        if not os.path.isdir(filename):
            f = os.path.basename(filename)
            if f not in clp:
                return False
            filename = os.path.dirname(filename)

        return all([os.path.exists(os.path.join(filename, fl)) for fl in clp])

    def saveInPlaceGen(self):
        """
        Save all chunks in the database.
        """
        batch = leveldb_mcpe.WriteBatch()
        for chunk in self._loadedChunks.itervalues():
            if chunk.dirty:
                self.worldFile.saveChunk(chunk, batch=batch)
                chunk.dirty = False
            yield

        with self.worldFile.world_db() as db:
            wop = self.worldFile.writeOptions
            db.Write(wop, batch)

    def containsChunk(self, cx, cz):
        """
        Determines if the chunk exist in this world.
        :param cx, cz: Coordinates of the chunk
        :return: bool (if chunk exists)
        """
        return (cx, cz) in self.allChunks

    @property
    def chunksNeedingLighting(self):
        """
        Generator containing all chunks that need lighting.
        :yield: (cx, cz) coordinates
        """
        for chunk in self._loadedChunks.itervalues():
            if chunk.needsLighting:
                yield chunk.chunkPosition


class PocketLeveldbChunk(LightedChunk):
    HeightMap = FakeChunk.HeightMap

    _Entities = _TileEntities = nbt.TAG_List()
    dirty = False

    def __init__(self, cx, cz, data, world):
        """
        :param cx, cz int, int Coordinates of the chunk
        :param data List of 3 strings. (83200 bytes of terrain data, tile-entity data, entity data)
        :param world PocketLeveldbWorld, instance of the world the chunk belongs too
        """
        self.chunkPosition = (cx, cz)
        self.world = world
        terrain = numpy.fromstring(data[0], dtype='uint8')

        if data[1] is not None:
            TileEntities = loadNBTCompoundList(data[1])
            self.TileEntities = nbt.TAG_List(TileEntities, list_type=nbt.TAG_COMPOUND)

        if data[2] is not None:
            Entities = loadNBTCompoundList(data[2])
            # PE saves entities with their int ID instead of string name. We swap them to make it work in mcedit.
            # Whenever we save an entity, we need to make sure to swap back.
            invertEntities = {v: k for k, v in entity.PocketEntity.entityList.items()}
            for ent in Entities:
                ent["id"] = nbt.TAG_String(invertEntities[ent["id"].value])
            self.Entities = nbt.TAG_List(Entities, list_type=nbt.TAG_COMPOUND)

        self.Blocks, terrain = terrain[:32768], terrain[32768:]
        self.Data, terrain = terrain[:16384], terrain[16384:]
        self.SkyLight, terrain = terrain[:16384], terrain[16384:]
        self.BlockLight, terrain = terrain[:16384], terrain[16384:]
        self.DirtyColumns, terrain = terrain[:256], terrain[256:]

        # Unused at the moment. Might need a special editor? Maybe hooked up to biomes?
        self.GrassColors = terrain[:1024]

        self.unpackChunkData()
        self.shapeChunkData()

    def unpackChunkData(self):
        """
        Unpacks the terrain data to match mcedit's formatting.
        """
        for key in ('SkyLight', 'BlockLight', 'Data'):
            dataArray = getattr(self, key)
            dataArray.shape = (16, 16, 64)
            s = dataArray.shape

            unpackedData = numpy.zeros((s[0], s[1], s[2] * 2), dtype='uint8')

            unpackedData[:, :, ::2] = dataArray
            unpackedData[:, :, ::2] &= 0xf
            unpackedData[:, :, 1::2] = dataArray
            unpackedData[:, :, 1::2] >>= 4
            setattr(self, key, unpackedData)

    def shapeChunkData(self):
        """
        Determines the shape of the terrain data.
        :return:
        """
        chunkSize = 16
        self.Blocks.shape = (chunkSize, chunkSize, self.world.Height)
        self.SkyLight.shape = (chunkSize, chunkSize, self.world.Height)
        self.BlockLight.shape = (chunkSize, chunkSize, self.world.Height)
        self.Data.shape = (chunkSize, chunkSize, self.world.Height)
        self.DirtyColumns.shape = chunkSize, chunkSize

    def savedData(self):
        """
        Returns the data of the chunk to save to the database.
        :return: str of 83200 bytes of chunk data.
        """

        def packData(dataArray):
            """
            Repacks the terrain data to Mojang's leveldb library's format.
            """
            assert dataArray.shape[2] == self.world.Height

            data = numpy.array(dataArray).reshape(16, 16, self.world.Height / 2, 2)
            data[..., 1] <<= 4
            data[..., 1] |= data[..., 0]
            return numpy.array(data[:, :, :, 1])

        if self.dirty:
            # elements of DirtyColumns are bitfields. Each bit corresponds to a
            # 16-block segment of the column. We set all of the bits because
            # we only track modifications at the chunk level.
            self.DirtyColumns[:] = 255

        with littleEndianNBT():
            entityData = ""
            tileEntityData = ""

            for ent in self.TileEntities:
                tileEntityData += ent.save(compressed=False)

            for ent in self.Entities:
                v = ent["id"].value
                ent["id"] = nbt.TAG_Int(entity.PocketEntity.entityList[v])
                entityData += ent.save(compressed=False)
                # We have to re-invert after saving otherwise the next save will fail.
                ent["id"] = nbt.TAG_String(v)

        terrain = ''.join([self.Blocks.tostring(),
                           packData(self.Data).tostring(),
                           packData(self.SkyLight).tostring(),
                           packData(self.BlockLight).tostring(),
                           self.DirtyColumns.tostring(),
                           self.GrassColors.tostring(),
                           ])

        return terrain, tileEntityData, entityData

    """
    Entities and TileEntities properties
    Unknown why these are properties, just implemented from MCLevel
    """

    @property
    def Entities(self):
        return self._Entities

    @Entities.setter
    def Entities(self, Entities):
        """
        :param Entities: list
        :return:
        """
        self._Entities = Entities

    @property
    def TileEntities(self):
        return self._TileEntities

    @TileEntities.setter
    def TileEntities(self, TileEntities):
        """
        :param TileEntities: list
        :return:
        """
        self._TileEntities = TileEntities
