// Terminal layer for the Plasma Mobile keyboard (motorola-rhodep).
//
// plasma-keyboard is Qt Virtual Keyboard, and its layouts are plain QML under
// /usr/share/plasma/keyboard/layouts/<locale>/. This file replaces the symbols
// layout for en_US and adds a third page with the keys a terminal needs and
// the stock keyboard has none of: Esc, Tab, the arrows, and real Ctrl
// combinations.
//
// Why not an existing on-screen keyboard: KWin advertises zwlr_layer_shell_v1
// and zwp_input_method_v1, but NOT zwp_virtual_keyboard_manager_v1, so wvkbd
// can draw itself and cannot inject a keystroke. squeekboard speaks
// input_method_v2, which KWin does not have either - that is why it crashes
// here, as README.md already noted. Extending the keyboard that is already
// integrated is the shorter path.
//
// The Ctrl keys do not fake control characters. They call
// InputContext.inputEngine.virtualKeyClick(key, text, modifiers), which is the
// documented API and produces a real key event with the modifier set, so
// Ctrl+Shift+C and Ctrl+Shift+V reach the terminal as copy and paste rather
// than as a stray character.

import QtQuick
import QtQuick.Layouts
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components

KeyboardLayoutLoader {
    id: root
    // 0 = symbols, 1 = more symbols, 2 = terminal
    property int page: 0
    onVisibleChanged: if (!visible) page = 0
    sourceComponent: page === 0 ? pageTerminal : (page === 1 ? pageSymbols : pageSymbols2)

    // One tap moves to the next page and wraps around.
    function nextPage() { root.page = (root.page + 1) % 3 }

    Component {
        id: pageSymbols
        KeyboardLayout {
            keyWeight: 160
            readonly property real normalKeyWidth: normalKey.width
            readonly property real functionKeyWidth: mapFromItem(normalKey, normalKey.width / 2, 0).x
            KeyboardRow {
                Key { key: Qt.Key_1; text: "1" }
                Key { id: normalKey; key: Qt.Key_2; text: "2" }
                Key { key: Qt.Key_3; text: "3" }
                Key { key: Qt.Key_4; text: "4" }
                Key { key: Qt.Key_5; text: "5" }
                Key { key: Qt.Key_6; text: "6" }
                Key { key: Qt.Key_7; text: "7" }
                Key { key: Qt.Key_8; text: "8" }
                Key { key: Qt.Key_9; text: "9" }
                Key { key: Qt.Key_0; text: "0" }
            }
            KeyboardRow {
                Key { key: Qt.Key_At; text: "@" }
                Key { key: Qt.Key_NumberSign; text: "#" }
                Key { key: Qt.Key_Dollar; text: "$" }
                Key { key: Qt.Key_Percent; text: "%" }
                Key { key: Qt.Key_Ampersand; text: "&" }
                Key { key: Qt.Key_Asterisk; text: "*" }
                Key { key: Qt.Key_Minus; text: "-" }
                Key { key: Qt.Key_Plus; text: "+" }
                Key { key: Qt.Key_ParenLeft; text: "(" }
                Key { key: Qt.Key_ParenRight; text: ")" }
            }
            KeyboardRow {
                Key {
                    displayText: "2/3"
                    functionKey: true
                    highlighted: true
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                    onClicked: root.nextPage()
                }
                Key { key: Qt.Key_Exclam; text: "!" }
                Key { key: Qt.Key_QuoteDbl; text: "\"" }
                Key { key: Qt.Key_Apostrophe; text: "'" }
                Key { key: Qt.Key_Colon; text: ":" }
                Key { key: Qt.Key_Semicolon; text: ";" }
                Key { key: Qt.Key_Comma; text: "," }
                Key { key: Qt.Key_Question; text: "?" }
                BackspaceKey { weight: functionKeyWidth; Layout.fillWidth: false }
            }
            KeyboardRow {
                SymbolModeKey { weight: functionKeyWidth; Layout.fillWidth: false; displayText: "ABC" }
                Key { key: Qt.Key_Slash; text: "/"; weight: normalKeyWidth; Layout.fillWidth: false }
                SpaceKey {}
                Key { key: Qt.Key_Period; text: "."; weight: normalKeyWidth; Layout.fillWidth: false }
                EnterKey { weight: functionKeyWidth; Layout.fillWidth: false }
            }
        }
    }

    Component {
        id: pageSymbols2
        KeyboardLayout {
            keyWeight: 160
            readonly property real normalKeyWidth: normalKey2.width
            readonly property real functionKeyWidth: mapFromItem(normalKey2, normalKey2.width / 2, 0).x
            KeyboardRow {
                Key { key: Qt.Key_AsciiTilde; text: "~" }
                Key { id: normalKey2; key: Qt.Key_QuoteLeft; text: "`" }
                Key { key: Qt.Key_Bar; text: "|" }
                Key { key: Qt.Key_Backslash; text: "\\" }
                Key { key: Qt.Key_Underscore; text: "_" }
                Key { key: Qt.Key_Equal; text: "=" }
                Key { key: Qt.Key_Less; text: "<" }
                Key { key: Qt.Key_Greater; text: ">" }
                Key { key: Qt.Key_BraceLeft; text: "{" }
                Key { key: Qt.Key_BraceRight; text: "}" }
            }
            KeyboardRow {
                Key { key: Qt.Key_BracketLeft; text: "[" }
                Key { key: Qt.Key_BracketRight; text: "]" }
                Key { text: "\u00a3" }
                Key { text: "\u20ac" }
                Key { text: "\u00a5" }
                Key { key: Qt.Key_AsciiCircum; text: "^" }
                Key { text: "\u00b0" }
                Key { text: "\u00b1" }
                Key { text: "\u00a9" }
                Key { text: "\u00ae" }
            }
            KeyboardRow {
                Key {
                    displayText: "3/3"
                    functionKey: true
                    highlighted: true
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                    onClicked: root.nextPage()
                }
                Key { text: "\u2022" }
                Key { text: "\u2013" }
                Key { text: "\u2014" }
                Key { text: "\u00ab" }
                Key { text: "\u00bb" }
                Key { text: "\u00bf" }
                Key { text: "\u00a1" }
                BackspaceKey { weight: functionKeyWidth; Layout.fillWidth: false }
            }
            KeyboardRow {
                SymbolModeKey { weight: functionKeyWidth; Layout.fillWidth: false; displayText: "ABC" }
                Key { key: Qt.Key_Slash; text: "/"; weight: normalKeyWidth; Layout.fillWidth: false }
                SpaceKey {}
                Key { key: Qt.Key_Period; text: "."; weight: normalKeyWidth; Layout.fillWidth: false }
                EnterKey { weight: functionKeyWidth; Layout.fillWidth: false }
            }
        }
    }

    Component {
        id: pageTerminal
        KeyboardLayout {
            keyWeight: 160
            readonly property real normalKeyWidth: normalKey3.width
            readonly property real functionKeyWidth: mapFromItem(normalKey3, normalKey3.width / 2, 0).x

            // Four and five keys per row, not ten. Labels like "Esc", "PgUp"
            // or "\u21e7^C" need the room: at ten per row they overflow the key
            // and run into each other.

            KeyboardRow {
                Key { id: normalKey3; key: Qt.Key_Escape; displayText: "Esc"; functionKey: true }
                Key { key: Qt.Key_Tab; text: "\t"; displayText: "Tab"; functionKey: true }
                Key { key: Qt.Key_Home; displayText: "Home"; functionKey: true }
                Key { key: Qt.Key_End; displayText: "End"; functionKey: true }
            }

            // The arrows kept together, so they can be used without looking.
            KeyboardRow {
                Key { key: Qt.Key_Left; displayText: "\u2190"; functionKey: true }
                Key { key: Qt.Key_Up; displayText: "\u2191"; functionKey: true }
                Key { key: Qt.Key_Down; displayText: "\u2193"; functionKey: true }
                Key { key: Qt.Key_Right; displayText: "\u2192"; functionKey: true }
            }

            // Real key events with the modifier set, not fabricated control
            // characters - that is why Ctrl+Shift+C reaches the terminal as
            // copy instead of as a stray character.
            KeyboardRow {
                Key {
                    displayText: "^C"; functionKey: true; noKeyEvent: true
                    onClicked: InputContext.inputEngine.virtualKeyClick(Qt.Key_C, "", Qt.ControlModifier)
                }
                Key {
                    displayText: "^V"; functionKey: true; noKeyEvent: true
                    onClicked: InputContext.inputEngine.virtualKeyClick(Qt.Key_V, "", Qt.ControlModifier)
                }
                Key {
                    displayText: "^X"; functionKey: true; noKeyEvent: true
                    onClicked: InputContext.inputEngine.virtualKeyClick(Qt.Key_X, "", Qt.ControlModifier)
                }
                Key {
                    displayText: "\u21e7^C"; functionKey: true; noKeyEvent: true
                    onClicked: InputContext.inputEngine.virtualKeyClick(Qt.Key_C, "", Qt.ControlModifier | Qt.ShiftModifier)
                }
                Key {
                    displayText: "\u21e7^V"; functionKey: true; noKeyEvent: true
                    onClicked: InputContext.inputEngine.virtualKeyClick(Qt.Key_V, "", Qt.ControlModifier | Qt.ShiftModifier)
                }
            }

            KeyboardRow {
                SymbolModeKey { weight: functionKeyWidth; Layout.fillWidth: false; displayText: "ABC" }
                Key {
                    displayText: "1/3"
                    functionKey: true
                    highlighted: true
                    weight: functionKeyWidth
                    Layout.fillWidth: false
                    onClicked: root.nextPage()
                }
                SpaceKey {}
                BackspaceKey { weight: functionKeyWidth; Layout.fillWidth: false }
                EnterKey { weight: functionKeyWidth; Layout.fillWidth: false }
            }
        }
    }
}
