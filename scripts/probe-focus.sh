#!/bin/bash
# Prints cursor position (AppKit, bottom-left origin) and frontmost app (name, pid, bundle) as JSON. No TCC needed.
osascript -l JavaScript -e 'ObjC.import("AppKit"); var p=$.NSEvent.mouseLocation; var f=$.NSWorkspace.sharedWorkspace.frontmostApplication; JSON.stringify({t:new Date().toISOString(), cursor:{x:p.x,y:p.y}, front:ObjC.unwrap(f.localizedName), front_pid:f.processIdentifier, front_bundle:ObjC.unwrap(f.bundleIdentifier)})'
