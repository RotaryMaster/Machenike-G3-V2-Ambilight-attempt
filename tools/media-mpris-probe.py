#!/usr/bin/env python3
"""Bounded, read-only MPRIS capability probe. No BLE or RGB writes."""
import argparse
import pathlib
import urllib.parse
import time

VIDEO = {'.mp4','.mkv','.webm','.avi','.mov','.m4v','.mpeg','.mpg','.ts','.ogv'}
AUDIO = {'.mp3','.flac','.ogg','.opus','.wav','.m4a','.aac'}

def classify(metadata):
    url = str(metadata.get('xesam:url', ''))
    try:
        parsed = urllib.parse.urlsplit(url)
        ext = pathlib.PurePosixPath(urllib.parse.unquote(parsed.path)).suffix.lower()
        host = (parsed.hostname or '').lower()
    except ValueError:
        return 'unknown'
    if ext in VIDEO:
        return 'video-extension'
    if ext in AUDIO:
        return 'audio-extension'
    if host in ('youtube.com','www.youtube.com','m.youtube.com') and parsed.path == '/watch':
        return 'video-candidate'
    if host == 'youtu.be':
        return 'video-candidate'
    return 'unknown'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error('seconds must be 1..120')
    import gi
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    prefix = 'org.mpris.MediaPlayer2.'
    path = '/org/mpris/MediaPlayer2'
    iface = 'org.mpris.MediaPlayer2.Player'
    seen = {}
    def report(name, props):
        md = props.get('Metadata', {})
        if not isinstance(md, dict):
            md = {}
        state = (str(props.get('PlaybackStatus','unknown')), classify(md), bool(md.get('xesam:url')), tuple(sorted(md)))
        if seen.get(name) != state:
            seen[name] = state
            print(name, 'status=', state[0], 'class=', state[1], 'url_present=', state[2], 'keys=', ','.join(state[3]), flush=True)
    def read(name):
        try:
            props = bus.call_sync(name,path,'org.freedesktop.DBus.Properties','GetAll',GLib.Variant('(s)',(iface,)),None,Gio.DBusCallFlags.NONE,700,None).unpack()[0]
            report(name,props)
        except GLib.Error as exc:
            print(name, 'unavailable:', exc.domain, exc.code, flush=True)
    def names():
        return bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','ListNames',None,None,Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
    owners = {}
    def register(name):
        try:
            owner = bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetNameOwner',GLib.Variant('(s)',(name,)),None,Gio.DBusCallFlags.NONE,700,None).unpack()[0]
            owners[name] = owner
            read(name)
        except GLib.Error:
            pass
    def changed(conn,sender,obj,interface,signal,parameters):
        player_iface, changed_props, invalidated = parameters.unpack()
        if player_iface != iface:
            return
        if not ({'Metadata','PlaybackStatus'} & (set(changed_props)|set(invalidated))):
            return
        for name, owner in list(owners.items()):
            if owner == sender:
                read(name)
    def owner_changed(conn,sender,obj,interface,signal,parameters):
        name,old,new = parameters.unpack()
        if not name.startswith(prefix):
            return
        owners.pop(name,None)
        seen.pop(name,None)
        if new:
            owners[name]=new
            read(name)
        else:
            print(name,'removed',flush=True)
    subscriptions = [
        bus.signal_subscribe(None,'org.freedesktop.DBus.Properties','PropertiesChanged',path,None,Gio.DBusSignalFlags.NONE,changed),
        bus.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus','NameOwnerChanged','/org/freedesktop/DBus',None,Gio.DBusSignalFlags.NONE,owner_changed)
    ]
    try:
        print('MPRIS probe: no hardware access; metadata values redacted',flush=True)
        for name in names():
            if name.startswith(prefix):
                register(name)
        if not owners:
            print('No MPRIS players initially',flush=True)
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(args.seconds, lambda: (loop.quit(),False)[1])
        loop.run()
    finally:
        for sub in subscriptions:
            bus.signal_unsubscribe(sub)

if __name__ == '__main__':
    main()
