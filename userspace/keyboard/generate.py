import re

HEADER_NOTE = '''// Ctrl and Alt arm rather than type, the way Termux's extra keys row works, so
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
// exactly once and then be overwritten.'''

SHARED = '''    property bool ctrlArmed: false
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

        // Ctrl+Shift+C and Ctrl+Shift+V cannot be delivered as combinations, and
        // as control characters they are indistinguishable from Ctrl+C and
        // Ctrl+V - control characters ignore case, 0x03 either way. So they go
        // out as Esc followed by the uppercase letter, a sequence nothing else
        // produces, and the shell turns that into copy and paste. See
        // userspace/terminal-clipboard.
        if (ctrlArmed && InputContext.shiftActive && plainText.length > 0) {
            disarm()
            InputContext.sendKeyClick(keyCode,
                                      String.fromCharCode(0x1b) + plainText.toUpperCase(),
                                      0)
            return
        }

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
'''

def term_row(indent):
    i = " " * indent
    # Word labels get room; arrow glyphs need much less. Equal widths are what
    # made "Ctrl" and "Esc" look oversized and cramped.
    def mod(name, prop):
        return (f'{i}    Key {{\n'
                f'{i}        displayText: "{name}"\n'
                f'{i}        functionKey: true\n'
                f'{i}        noKeyEvent: true\n'
                f'{i}        noModifier: true\n'
                f'{i}        weight: 210\n'
                f'{i}        highlighted: root.{prop}\n'
                f'{i}        // A dot in the corner, so the label keeps its width.\n'
                f'{i}        smallText: "\\u25cf"\n'
                f'{i}        smallTextVisible: root.{prop}\n'
                f'{i}        onClicked: root.{prop} = !root.{prop}\n'
                f'{i}    }}\n')
    return (f'{i}// Esc, Tab, the modifiers and the arrows, in the same place on every page.\n'
            f'{i}KeyboardRow {{\n'
            f'{i}    Key {{ key: Qt.Key_Escape; displayText: "Esc"; functionKey: true; noModifier: true; weight: 210 }}\n'
            f'{i}    Key {{ key: Qt.Key_Tab; text: "\\t"; displayText: "Tab"; functionKey: true; noModifier: true; weight: 210 }}\n'
            + mod("Ctrl", "ctrlArmed") + mod("Alt", "altArmed") +
            f'{i}    Key {{ key: Qt.Key_Left; displayText: "\\u2190"; functionKey: true; noModifier: true; weight: 120 }}\n'
            f'{i}    Key {{ key: Qt.Key_Up; displayText: "\\u2191"; functionKey: true; noModifier: true; weight: 120 }}\n'
            f'{i}    Key {{ key: Qt.Key_Down; displayText: "\\u2193"; functionKey: true; noModifier: true; weight: 120 }}\n'
            f'{i}    Key {{ key: Qt.Key_Right; displayText: "\\u2192"; functionKey: true; noModifier: true; weight: 120 }}\n'
            f'{i}}}')

def blocks(src, pattern):
    out = []
    for m in re.finditer(pattern, src, re.M):
        i = src.index('{', m.start()); depth = 0
        for j in range(i, len(src)):
            if src[j] == '{': depth += 1
            elif src[j] == '}':
                depth -= 1
                if depth == 0: break
        out.append((m.start(), j + 1, src[m.start():j + 1]))
    return out

def to_termkeys(block):
    res, pos = [], 0
    for s, e, b in blocks(block, r'^(\s*)Key \{'):
        res.append(block[pos:s])
        res.append(re.sub(r'^(\s*)Key \{', r'\1TermKey {', b) if ('text:' in b and 'onClicked' not in b) else b)
        pos = e
    res.append(block[pos:])
    return "".join(res)

stock = open('/tmp/opencode/stock_main.qml').read()
rows = [b for _, _, b in blocks(stock, r'^    KeyboardRow \{')]
letters = [to_termkeys(r) for r in rows[:3]]

main = ('''// Terminal-friendly Latin layout for the Plasma Mobile keyboard
// (motorola-rhodep). Derived from Qt's stock main.qml.
//
// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
//
''' + HEADER_NOTE + '''
//
// No digit row: the digits are one tap away on &123, and long press on q..p
// still reaches them through alternativeKeys.

import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components
import QtQuick.Layouts

KeyboardLayout {
    id: root
    inputMode: InputEngine.InputMode.Latin
    keyWeight: 160
    readonly property real normalKeyWidth: normalKey.width
    readonly property real functionKeyWidth: mapFromItem(normalKey, normalKey.width / 2, 0).x

''' + SHARED + "\n" + term_row(4) + "\n" + "\n".join(letters) + "\n" + rows[3] + "\n}\n")
open('main.qml', 'w').write(main)

ss = open('/tmp/opencode/stock_symbols.qml').read()
pages = blocks(ss, r'^    Component \{')

def rebuild_page(block):
    block = to_termkeys(block)
    first = blocks(block, r'^\s+KeyboardRow \{')[0]
    return block[:first[0]] + term_row(12).lstrip() + "\n" + " " * 12 + block[first[0]:].lstrip()

body = "\n".join(rebuild_page(b) for _, _, b in pages)
symbols = ('''// Symbols layout for the Plasma Mobile keyboard (motorola-rhodep).
// Qt's stock two pages, with the terminal row added to both.
//
// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
//
''' + HEADER_NOTE + '''
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

''' + SHARED + "\n" + body + "\n}\n")
open('symbols.qml', 'w').write(symbols)
print("main.qml y symbols.qml regenerados")
