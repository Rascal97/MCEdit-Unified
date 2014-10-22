import sys
import os
import directories
import config
import albow
import mceutils
from pygame import display, event, key, KMOD_ALT, KMOD_CTRL, KMOD_LALT, KMOD_META, KMOD_RALT, KMOD_SHIFT, mouse, \
    MOUSEMOTION
from albow.dialogs import Dialog, QuickDialog, wrapped_label
from albow import alert, ask, AttrRef, Button, Column, get_font, Grid, input_text, IntField, Menu, root, Row, \
    TableColumn, TableView, TextField, TimeField, Widget, CheckBox
from albow.controls import Label, SmallValueDisplay, ValueDisplay
from albow.translate import tr
from glbackground import Panel

ESCAPE = '\033'

def remapMouseButton(button):
    buttons = [0, 1, 3, 2, 4, 5]  # mouse2 is right button, mouse3 is middle
    if button < len(buttons):
        return buttons[button]
    return button
    
def getKey(evt):
    keyname = key.name(evt.key)
    if 'left' in keyname and len(keyname) > 5:
        keyname = keyname[5:]
    elif 'right' in keyname and len(keyname) > 6:
        keyname = keyname[6:]
    try:
        keyname = keyname.replace(keyname[0], keyname[0].upper(), 1)
    finally:
        newKeyname = ""
        if evt.shift == True and keyname != "Shift":
            newKeyname += "Shift-"
        if evt.ctrl == True and keyname != "Ctrl":
            newKeyname += "Ctrl-"
        elif evt.cmd == True and keyname != "Meta":
            newKeyname += "Ctrl-"
        if evt.alt == True and keyname != "Alt":
            newKeyname += "Alt-"
    
        keyname = newKeyname + keyname
        return keyname

class KeyConfigPanel(Dialog):
    keyConfigKeys = [
        "<Movement Controls>",
        "Forward",
        "Back",
        "Left",
        "Right",
        "Up",
        "Down",
        "Brake",
        "",
        "<Camera Controls>",
        "Pan Up",
        "Pan Down",
        "Pan Left",
        "Pan Right",
        "",
        "<Tool Controls>",
        "Rotate",
        "Roll",
        "Flip",
        "Mirror",
        "Swap",
        "Delete Blocks",
        "Increase Reach",
        "Decrease Reach",
        "Reset Reach",
        "",
        "<Function Controls>",
        "Quit",
        "Swap View",
        "Select All",
        "Deselect",
        "Cut",
        "Copy",
        "Paste",
        "Reload World",
        "Open",
        "Quick Load",
        "Undo",
        "Save",
        "New World",
        "Close World",
        "World Info",
        "Goto Panel",
        "Export Selection"
    ]

    presets = {"WASD": [
        ("Forward", "W"),
        ("Back", "S"),
        ("Left", "A"),
        ("Right", "D"),
        ("Up", "Space"),
        ("Down", "Shift"),
        ("Brake", "C"),

        ("Rotate", "E"),
        ("Roll", "R"),
        ("Flip", "F"),
        ("Mirror", "G"),
        ("Swap", "X"),
        ("Increase Reach", "Scroll Up"),
        ("Decrease Reach", "Scroll Down"),
        ("Reset Reach", "Mouse3"),
        ("Delete Blocks", "Delete"),
    ],
               "Arrows": [
                   ("Forward", "Up"),
                   ("Back", "Down"),
                   ("Left", "Left"),
                   ("Right", "Right"),
                   ("Up", "Page Up"),
                   ("Down", "Page Down"),
                   ("Brake", "Space"),

                   ("Rotate", "Home"),
                   ("Roll", "End"),
                   ("Flip", "Insert"),
                   ("Mirror", "Delete"),
                   ("Swap", "\\"),
                   ("Increase Reach", "Scroll Up"),
                   ("Decrease Reach", "Scroll Down"),
                   ("Reset Reach", "Mouse3"),
                   ("Delete Blocks", "Backspace")
               ],
               "Numpad": [
                   ("Forward", "[8]"),
                   ("Back", "[5]"),
                   ("Left", "[4]"),
                   ("Right", "[6]"),
                   ("Up", "[7]"),
                   ("Down", "[1]"),
                   ("Brake", "[0]"),

                   ("Rotate", "[-]"),
                   ("Roll", "[+]"),
                   ("Flip", "[/]"),
                   ("Mirror", "[*]"),
                   ("Swap", "[.]"),
                   ("Increase Reach", "Scroll Up"),
                   ("Decrease Reach", "Scroll Down"),
                   ("Reset Reach", "Mouse3"),
                   ("Delete Blocks", "Delete")
               ]}

    selectedKeyIndex = 0

    def __init__(self):
        Dialog.__init__(self)
        keyConfigTable = albow.TableView(
            columns=[albow.TableColumn("Command", 200, "l"), albow.TableColumn("Assigned Key", 150, "r")])
        keyConfigTable.num_rows = lambda: len(self.keyConfigKeys)
        keyConfigTable.row_data = self.getRowData
        keyConfigTable.row_is_selected = lambda x: x == self.selectedKeyIndex
        keyConfigTable.click_row = self.selectTableRow
        tableWidget = albow.Widget()
        tableWidget.add(keyConfigTable)
        tableWidget.shrink_wrap()

        self.keyConfigTable = keyConfigTable

        buttonRow = (albow.Button("Assign Key...", action=self.askAssignSelectedKey),
                     albow.Button("Done", action=self.done))

        buttonRow = albow.Row(buttonRow)

        choiceButton = mceutils.ChoiceButton(["WASD", "Arrows", "Numpad"], choose=self.choosePreset)
        if config.config.get("Keys", "Forward") == "Up":
            choiceButton.selectedChoice = "Arrows"
        if config.config.get("Keys", "Forward") == "[8]":
            choiceButton.selectedChoice = "Numpad"

        choiceRow = albow.Row((albow.Label("Presets: "), choiceButton))
        self.choiceButton = choiceButton

        col = albow.Column((tableWidget, choiceRow, buttonRow))
        self.add(col)
        self.shrink_wrap()

    def done(self):
        config.saveConfig()
        self.dismiss()
    
    def choosePreset(self):
        preset = self.choiceButton.selectedChoice
        keypairs = self.presets[preset]
        for configKey, key in keypairs:
            config.config.set("Keys", configKey, key)

    def getRowData(self, i):
        configKey = self.keyConfigKeys[i]
        if self.isConfigKey(configKey):
            key = config.config.get("Keys", configKey)
            if key == 'mouse4':
                key = 'Scroll Up'
                config.config.set("Keys", configKey, "Scroll Up")
            if key == 'mouse5':
                key = 'Scroll Down'
                config.config.set("Keys", configKey, "Scroll Down")
        else:
            key = ""
        return configKey, key

    def isConfigKey(self, configKey):
        return not (len(configKey) == 0 or configKey[0] == "<")

    def selectTableRow(self, i, evt):
        self.selectedKeyIndex = i
        if evt.num_clicks == 2:
            self.askAssignSelectedKey()

    def askAssignSelectedKey(self):
        self.askAssignKey(self.keyConfigKeys[self.selectedKeyIndex])

    def askAssignKey(self, configKey, labelString=None):
        if not self.isConfigKey(configKey):
            return

        panel = Panel()
        panel.bg_color = (0.5, 0.5, 0.6, 1.0)

        if labelString is None:
            labelString = tr("Press a key to assign to the action \"{0}\"\n\nPress ESC to cancel.").format(configKey)
        label = albow.Label(labelString)
        panel.add(label)
        panel.shrink_wrap()

        def panelKeyUp(evt):
            keyname = getKey(evt)
            panel.dismiss(keyname)

        def panelMouseUp(evt):
            button = remapMouseButton(evt.button)
            if button == 3:
                keyname = "Mouse3"
            elif button == 4:
                keyname = "Scroll Up"
            elif button == 5:
                keyname = "Scroll Down"
            if button > 2:
                panel.dismiss(keyname)

        panel.key_up = panelKeyUp
        panel.mouse_up = panelMouseUp

        keyname = panel.present()
        if keyname != "Escape":
            occupiedKeys = [(v, k) for (k, v) in config.config.items("Keys") if v.upper() == keyname]
            oldkey = config.config.get("Keys", configKey)
            config.config.set("Keys", configKey, keyname)
            for keyname, setting in occupiedKeys:
                if self.askAssignKey(setting,
                                     tr("The key {0} is no longer bound to {1}. "
                                     "Press a new key for the action \"{1}\"\n\n"
                                     "Press ESC to cancel.")
                                     .format(keyname, setting)):
                    config.config.set("Keys", configKey, oldkey)
                    return True  #Only disabled as currently you can't input modifiers, reenable if fixed and edit leveleditor.py as needed
        else:
            return True