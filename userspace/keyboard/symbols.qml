// Symbols layout for the Plasma Mobile keyboard (motorola-rhodep).
// Qt's stock two pages, with the terminal row added to both.
//
// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
//
// Ctrl and Alt arm rather than type, the way Termux's extra keys row works, so
// any combination is reachable.
//
// How the combination is delivered, which took a protocol trace and a read of
// plasma-keyboard's source to get right. Passing Qt.ControlModifier to
// virtualKeyClick does nothing: src/inputlisteneritem.cpp sends
//
//     m_input.keysym(timestamp, key, InputPlugin::Pressed, 0);
//                                                          ^ hardcoded
//
// so the protocol's modifier field is always zero and no mask ever arrives. The
// terminal saw a bare "C". Holding Control_L down around the key does not work
// either: InputEngine delivers a key press on release, so the trace showed the
// letter going out first and Control_L after it.
//
// The same file shows the way through. A key whose event carries text is
// committed as a string instead:
//
//     if (event->text().isEmpty() || key == XKB_KEY_Return)
//         m_input.keysym(...);
//     else
//         m_input.commit(event->text());
//
// and Ctrl+C on a tty *is* a byte: 0x03. So these keys send the control
// character as text. The 0x40..0x5f block folds onto 0x00..0x1f, which is
// exactly what Ctrl does, and Alt is an Escape prefix, which is what every
// shell expects.
//
// What this cannot do is Ctrl+Shift+C for terminal copy: that is not a
// character, it is a combination the terminal interprets, and combinations are
// what the hardcoded zero above throws away. It needs a patched
// plasma-keyboard.
//
// The armed state is a property of the root object, not a ModeKey's own "mode":
// ModeKey does "onClicked: mode = !mode", so a binding to mode would work
// exactly once and then be overwritten.
//
// There is no extra page of terminal keys any more: the row sits on every page
// instead, which is fewer taps and one less thing to learn.

import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components
import QtQuick.Layouts

KeyboardLayoutLoader {
    id: root
    property bool secondPage
    onVisibleChanged: if (!visible) { secondPage = false; disarm() }
    sourceComponent: secondPage ? page2 : page1

    property bool ctrlArmed: false
    property bool altArmed: false

    function armed() { return ctrlArmed || altArmed }

    function disarm() {
        ctrlArmed = false
        altArmed = false
    }

    // The 0x40..0x5f block is what Ctrl folds onto 0x00..0x1f on a tty:
    // Ctrl+C is 0x03, Ctrl+[ is 0x1b (which is why Esc and Ctrl+[ are the same
    // key on a terminal), Ctrl+_ is 0x1f.
    function controlCharacter(keyCode) {
        if (keyCode >= 0x40 && keyCode <= 0x5f)
            return String.fromCharCode(keyCode & 0x1f)
        return ""
    }

    // Ctrl+V is left alone: it sends 0x16 like any other control character, and
    // zsh turns that into a clipboard paste - see userspace/terminal-clipboard.
    // Reading the clipboard here was tried and cannot work: the keyboard has no
    // keyboard focus, so KWin never sends it a selection offer. The trace showed
    // not one data_offer, and every read came back empty.
    // Sent as text, not as a modifier mask, because the mask is discarded on the
    // way out - see the note at the top of the file.
    function sendCombination(keyCode, plainText) {
        var out = plainText
        if (ctrlArmed) {
            var c = controlCharacter(keyCode)
            if (c !== "")
                out = c
        }
        if (altArmed)
            out = String.fromCharCode(0x1b) + out // Esc prefix: Alt on a tty
        disarm()
        if (out.length === 0)
            return
        InputContext.sendKeyClick(keyCode, out, 0)
    }

    // A character key that turns into part of a combination while a modifier is
    // armed: noKeyEvent stops it typing its own character, and clicked still
    // fires, which is what sends the combination.
    component TermKey: Key {
        noKeyEvent: root.armed()
        onClicked: {
            if (!root.armed())
                return
            root.sendCombination(key, uppercased ? text.toUpperCase() : text)
        }
    }

    Component {
        id: page1
        KeyboardLayout {
            keyWeight: 160
            readonly property real normalKeyWidth: normalKey.width
            readonly property real functionKeyWidth: mapFromItem(normalKey, normalKey.width / 2, 0).x
// Esc, Tab, the modifiers and the arrows, in the same place on every page.
            KeyboardRow {
                Key { key: Qt.Key_Escape; displayText: "Esc"; functionKey: true; noModifier: true; weight: 210 }
                Key { key: Qt.Key_Tab; text: "\t"; displayText: "Tab"; functionKey: true; noModifier: true; weight: 210 }
                Key {
                    displayText: "Ctrl"
                    functionKey: true
                    noKeyEvent: true
                    noModifier: true
                    weight: 210
                    highlighted: root.ctrlArmed
                    // A dot in the corner, so the label keeps its width.
                    smallText: "\u25cf"
                    smallTextVisible: root.ctrlArmed
                    onClicked: root.ctrlArmed = !root.ctrlArmed
                }
                Key {
                    displayText: "Alt"
                    functionKey: true
                    noKeyEvent: true
                    noModifier: true
                    weight: 210
                    highlighted: root.altArmed
                    // A dot in the corner, so the label keeps its width.
                    smallText: "\u25cf"
                    smallTextVisible: root.altArmed
                    onClicked: root.altArmed = !root.altArmed
                }
                Key { key: Qt.Key_Left; displayText: "\u2190"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Up; displayText: "\u2191"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Down; displayText: "\u2193"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Right; displayText: "\u2192"; functionKey: true; noModifier: true; weight: 120 }
            }
            KeyboardRow {
                TermKey {
                    key: Qt.Key_1
                    text: "1"
                }
                TermKey {
                    id: normalKey
                    key: Qt.Key_2
                    text: "2"
                }
                TermKey {
                    key: Qt.Key_3
                    text: "3"
                }
                TermKey {
                    key: Qt.Key_4
                    text: "4"
                }
                TermKey {
                    key: Qt.Key_5
                    text: "5"
                }
                TermKey {
                    key: Qt.Key_6
                    text: "6"
                }
                TermKey {
                    key: Qt.Key_7
                    text: "7"
                }
                TermKey {
                    key: Qt.Key_8
                    text: "8"
                }
                TermKey {
                    key: Qt.Key_9
                    text: "9"
                }
                TermKey {
                    key: Qt.Key_0
                    text: "0"
                }
            }
            KeyboardRow {
                TermKey {
                    key: Qt.Key_At
                    text: "@"
                }
                TermKey {
                    key: Qt.Key_NumberSign
                    text: "#"
                }
                TermKey {
                    key:  Qt.Key_Percent
                    text: "%"
                }
                TermKey {
                    key: Qt.Key_Ampersand
                    text: "&"
                }
                TermKey {
                    key: Qt.Key_Asterisk
                    text: "*"
                }
                TermKey {
                    key: Qt.Key_Underscore
                    text: "_"
                }
                TermKey {
                    key: Qt.Key_Minus
                    text: "-"
                }
                TermKey {
                    key: Qt.Key_Plus
                    text: "+"
                }
                TermKey {
                    key: Qt.Key_ParenLeft
                    text: "("
                }
                TermKey {
                    key: Qt.Key_ParenRight
                    text: ")"
                }
            }
            KeyboardRow {
                Key {
                    displayText: "1/2"
                    functionKey: true
                    onClicked: secondPage = !secondPage
                    highlighted: true
                }
                TermKey {
                    key:  Qt.Key_QuoteDbl
                    text: '"'
                }
                TermKey {
                    key: Qt.Key_Less
                    text: "<"
                }
                TermKey {
                    key: Qt.Key_Greater
                    text: ">"
                }
                TermKey {
                    key: Qt.Key_Apostrophe
                    text: "'"
                }
                TermKey {
                    key: Qt.Key_Colon
                    text: ":"
                }
                TermKey {
                    key: Qt.Key_Slash
                    text: "/"
                }
                TermKey {
                    key: Qt.Key_Exclam
                    text: "!"
                }
                TermKey {
                    key: Qt.Key_Question
                    text: "?"
                }
                BackspaceKey {
                }
            }
            KeyboardRow {
                SymbolModeKey {
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                    displayText: InputContext.inputEngine.inputMode === InputEngine.InputMode.Cyrillic ? "АБВ" : "ABC"
                }
                ChangeLanguageKey {
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                }
                TermKey {
                    key: Qt.Key_Comma
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                    text: ","
                    smallText: "\u2699"
                    smallTextVisible: keyboard.isFunctionPopupListAvailable()
                    highlighted: true
                }
                SpaceKey {
                }
                TermKey {
                    key: Qt.Key_Period
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                    text: "."
                    highlighted: true
                }
                HideKeyboardKey {
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                }
                EnterKey {
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                }
            }
        }
    }
    Component {
        id: page2
        KeyboardLayout {
            keyWeight: 160
            readonly property real normalKeyWidth: normalKey.width
            readonly property real functionKeyWidth: mapFromItem(normalKey, normalKey.width / 2, 0).x
// Esc, Tab, the modifiers and the arrows, in the same place on every page.
            KeyboardRow {
                Key { key: Qt.Key_Escape; displayText: "Esc"; functionKey: true; noModifier: true; weight: 210 }
                Key { key: Qt.Key_Tab; text: "\t"; displayText: "Tab"; functionKey: true; noModifier: true; weight: 210 }
                Key {
                    displayText: "Ctrl"
                    functionKey: true
                    noKeyEvent: true
                    noModifier: true
                    weight: 210
                    highlighted: root.ctrlArmed
                    // A dot in the corner, so the label keeps its width.
                    smallText: "\u25cf"
                    smallTextVisible: root.ctrlArmed
                    onClicked: root.ctrlArmed = !root.ctrlArmed
                }
                Key {
                    displayText: "Alt"
                    functionKey: true
                    noKeyEvent: true
                    noModifier: true
                    weight: 210
                    highlighted: root.altArmed
                    // A dot in the corner, so the label keeps its width.
                    smallText: "\u25cf"
                    smallTextVisible: root.altArmed
                    onClicked: root.altArmed = !root.altArmed
                }
                Key { key: Qt.Key_Left; displayText: "\u2190"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Up; displayText: "\u2191"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Down; displayText: "\u2193"; functionKey: true; noModifier: true; weight: 120 }
                Key { key: Qt.Key_Right; displayText: "\u2192"; functionKey: true; noModifier: true; weight: 120 }
            }
            KeyboardRow {
                TermKey {
                    key: Qt.Key_AsciiTilde
                    text: "~"
                }
                TermKey {
                    id: normalKey
                    key: Qt.Key_Agrave
                    text: "`"
                }
                TermKey {
                    key: Qt.Key_Bar
                    text: "|"
                }
                TermKey {
                    key: 0x7B
                    text: "·"
                }
                TermKey {
                    key: 0x221A
                    text: "√"
                }
                TermKey {
                    key: Qt.Key_division
                    text: "÷"
                }
                TermKey {
                    key: Qt.Key_multiply
                    text: "×"
                }
                TermKey {
                    key: Qt.Key_onehalf
                    text: "½"
                    alternativeKeys: "¼⅓½¾⅞"
                }
                TermKey {
                    key: Qt.Key_BraceLeft
                    text: "{"
                }
                Key {
                    key: Qt.Key_BraceRight
                    text: "}"
                }                TermKey {
                    key: Qt.Key_BraceRight
                    text: "}"
                }
            }
            KeyboardRow {
                TermKey {
                    key: Qt.Key_Dollar
                    text: "$"
                }
                TermKey {
                    key: 0x20AC
                    text: "€"
                }
                TermKey {
                    key: 0xC2
                    text: "£"
                }
                TermKey {
                    key: 0xA2
                    text: "¢"
                }
                TermKey {
                    key: 0xA5
                    text: "¥"
                }
                TermKey {
                    key: Qt.Key_AsciiCircum
                    text: "^"
                }
                TermKey {
                    key: Qt.Key_Equal
                    text: "="
                }
                TermKey {
                    key: Qt.Key_section
                    text: "§"
                }
                TermKey {
                    key: Qt.Key_BracketLeft
                    text: "["
                }
                TermKey {
                    key: Qt.Key_BracketRight
                    text: "]"
                }
            }
            KeyboardRow {
                Key {
                    displayText: "2/2"
                    functionKey: true
                    onClicked: secondPage = !secondPage
                    highlighted: true
                }
                TermKey {
                    key: 0x2122
                    text: '™'
                }
                TermKey {
                    key: 0x00AE
                    text: '®'
                }
                TermKey {
                    key: Qt.Key_guillemotleft
                    text: '«'
                }
                TermKey {
                    key: Qt.Key_guillemotright
                    text: '»'
                }
                TermKey {
                    key: Qt.Key_Semicolon
                    text: ";"
                }
                TermKey {
                    key: 0x201C
                    text: '“'
                }
                TermKey {
                    key: 0x201D
                    text: '”'
                }
                TermKey {
                    key: Qt.Key_Backslash
                    text: "\\"
                }
                BackspaceKey {
                }
            }
            KeyboardRow {
                SymbolModeKey {
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                    displayText: InputContext.inputEngine.inputMode === InputEngine.InputMode.Cyrillic ? "АБВ" : "ABC"
                }
                ChangeLanguageKey {
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                }
                TermKey {
                    key: Qt.Key_Comma
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                    text: ","
                    smallText: "\u2699"
                    smallTextVisible: keyboard.isFunctionPopupListAvailable()
                    highlighted: true
                }
                SpaceKey {
                }
                TermKey {
                    key: 0x2026
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                    text: "\u2026"
                    highlighted: true
                }
                HideKeyboardKey {
                    weight: normalKeyWidth
                    Layout.fillWidth: false
                }
                EnterKey {
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                }
            }
        }
    }
}
