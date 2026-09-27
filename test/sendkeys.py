#!/usr/bin/env python3
"""Type text into a QEMU VM through its monitor socket (US layout).
usage: sendkeys.py <monitor-socket> <text>   ("\n" = Enter, "\x1b" = Esc)"""
import socket, sys, time

KEYS = {' ': 'spc', '\n': 'ret', '\x1b': 'esc', '\t': 'tab', '/': 'slash', '-': 'minus',
        '.': 'dot', ',': 'comma', ';': 'semicolon', '=': 'equal', "'": 'apostrophe',
        '`': 'grave_accent', '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash'}
SHIFTED = {'|': 'backslash', ':': 'semicolon', '_': 'minus', '>': 'dot', '<': 'comma',
           '"': 'apostrophe', '?': 'slash', '+': 'equal', '~': 'grave_accent',
           '{': 'bracket_left', '}': 'bracket_right', '!': '1', '@': '2', '#': '3',
           '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0'}

def key(ch):
    if ch in KEYS: return KEYS[ch]
    if ch in SHIFTED: return 'shift-' + SHIFTED[ch]
    if ch.isupper(): return 'shift-' + ch.lower()
    return ch

s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1]); time.sleep(0.2); s.recv(65536)
for ch in sys.argv[2]:
    s.send(f"sendkey {key(ch)}\n".encode()); time.sleep(0.05)
time.sleep(0.3)
