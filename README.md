# Roblox Piano for Linux

A MIDI autoplayer for Roblox virtual pianos, built for Linux Mint. It reads a
`.mid` file and plays it on the in-game piano by pressing the corresponding
QWERTY keys, the same job MIDI++ does on Windows.

Everything installs into a `venv` in this folder. To get rid of it, delete the
folder. The one exception is a udev rule; removing that is two commands at the
bottom of this file.

## Install

```bash
./install.sh          # builds the venv, installs PyQt6 / mido / pynput
./setup-uinput.sh     # one-time permission for the virtual keyboard
```

Log out and back in after `setup-uinput.sh` — group membership doesn't apply to
an already-running session. Then:

```bash
./run.sh
```

There's also a "Roblox Piano" entry in your applications menu. If `install.sh`
complains about venv, run `sudo apt install python3-venv` first.

## Start here: the two test buttons

Both are in the **Input** tab.

**Play a test scale** plays eight white keys, pauses, then five black keys.
The second half is the real test. If the black keys sound like the white keys
below them, your modifier dwell is too short — see Timing below.

**Range test** plays only the notes outside the standard C2–C7 range, so you
can check the top and bottom octaves of the 88-key layout on their own. The
Details tab lists which note each one should be.

## Timing: why the settings in that tab exist

Roblox checks its input once per frame, and that single fact causes all three
classes of problem people hit with autoplayers.

**Modifier dwell** — when the game handles a key-down it asks "is shift held
*right now*", rather than reading a shift state carried on the event. If shift
goes down and back up between two frames, the game never sees it, and a black
key comes out as the white key below it. So shift is pressed, held across a
frame boundary, and only then released. It's also pressed *early*, so the key
itself still strikes on the beat instead of a dwell behind it.

On a fast run of black keys shift is simply held down for the whole run rather
than retaken for every note, the way a hand would. That matters because the
press and release cost a dwell each, and in a quick passage there isn't room
for both between one note and the next. It only does this when the notes are
genuinely too close for the normal cycle to fit, and never across a white key.

**Minimum note** — a key pressed and released inside a single frame can be
missed entirely. Every note is held for a floor duration no matter how short
it is in the MIDI. This is what rescues fast passages and grace notes.

**Retrigger gap** — a repeated note has to be released and struck again with a
real gap between, or the game sees no transition and the second strike never
lands.

The defaults assume roughly 60 fps. If you run at 30, use the **30 fps** preset
button, which is more forgiving. Higher frame rates let you tighten everything
for snappier playback.

**Chord window** groups notes that start close together so a chord costs one
shift press instead of one per note.

Note that a chord mixing white and black keys is unavoidably spread over a few
tens of milliseconds — the white keys have to be struck before shift goes down.
It sounds like a slight roll. That's inherent, not a bug.

## The layouts

**Roblox 61-key** is the standard virtual-piano layout, C2 to C7: white keys
walk `1234567890qwertyuiop...` and each black key is shift plus the white key
below it. This is verified, nearly every Roblox piano uses it, and it's the
default.

**Roblox 88-key** is the layout Piano Rooms uses, A0 to C8: the middle five
octaves are identical to the 61-key layout, and the 27 notes outside them walk
the same row again with ctrl held, one key per semitone — A0 is `ctrl+1`, up
through `ctrl+t` at B1, resuming at `ctrl+y` for C#7 and ending at `ctrl+j` for
C8. Black keys out there take a key of their own, so ctrl+shift never arises.
Both layouts were checked note for note against a working MIDI++ config; the
Range test button plays those 27 notes on their own to confirm it in your game.

**Out of range** decides what happens to notes the layout cannot reach: fold
them into the nearest octave, or drop them. The line under that box works it
out for the song you have open and says which to use. Folding is not free — a
note pushed back into range can land on a key another note is already holding,
so it buys notes in the wrong octave at the cost of damaging notes that were
fine. That falls hardest on bass, which folds *up* into the busiest part of the
keyboard, so a song overflowing at the bottom is usually better off dropping.
Treble folds down into a sparser register and rarely collides.

Switching layout changes which notes are reachable, so a transpose fitted to
the layout before it is stale — going from 88 to 61 leaves everything below C2
folding an octave, quietly. With **Fit automatically** ticked the fit is redone
on every switch, and the Log says what it chose. Untick it to keep a transpose
you set by hand, and watch the line under the title for how many notes ended up
out of range.

Either layout can be adjusted. **Edit mapping** (Input tab) lets you select a
row, press **Capture key**, and press what that note should actually use;
saving creates a new custom layout and leaves the built-ins alone. **Import a
MIDI++ config.json** reads a mapping straight out of an old config. Custom
layouts live in `~/.config/roblox-piano-linux/layouts/`.

## Controls

Global hotkeys work while Roblox has focus, which is the point.

| Key | Action |
|-----|--------|
| F1 | Play / pause |
| F2 | Stop (also releases every held key) |
| F3 / F4 | Transpose down / up a semitone |
| F5 | Restart |
| F6 / F7 | Skip back / forward |

**Count-in** gives you a few seconds to switch to Roblox after pressing Play.
Set it to 0 if you use the hotkeys instead.

**Keep this window above Roblox** plus the opacity slider (Playback tab) lets
you leave a translucent strip over the game rather than alt-tabbing.

## Tracks and channels

**Play** unticks a part. **Solo** overrides everything — if anything is soloed,
only soloed parts play. Each row shows the instrument family, note count and
pitch range.

Channels are separate from tracks, because one track can carry several. The
drum channel is excluded by default; if a rock arrangement is missing notes,
try ticking **Include the drum channel**, since some files put guitar on
channel 10.

## Sustain

If your game has a sustain pedal key, set it in the Input tab. On the 88-key
pianos it's **space**.

**Use a sustain pedal** is repopulated every time you change layout: on for
the 88-key, off for the 61-key. That default matters, because most 61-key
pianos have no pedal at all and space is *jump* — a pedal left on from the
previous layout makes your character hop through the whole song. Untick or
tick it afterwards if your game differs; the setting stands until the next
time you switch layout, and the key you chose is remembered either way.

**Sustain cutoff** is the CC64 value above which the pedal counts as down. 64
is the standard midpoint; raise it if a file rides the pedal lightly.

## Troubleshooting

Check the **Log** tab first. It reports the backend state, the timing values in
use, and a count at the end of every playback of how many notes were struck,
skipped as out of range, cut short by a key clash, or struck more than 25ms
behind where they belonged.

That last count is the one to watch if the playing sounds loose rather than
plainly wrong. A handful on a dense arrangement is normal. Hundreds means the
machine cannot keep up with the file: thin it out in the Tracks tab, or drop
the speed slightly.

**Cut short by a key clash** means two different notes wanted the same physical
key — C4 and C#4 share one, shift apart — and one had to give it up. That is a
real limit of the keyboard and nothing can be done about it. It does *not*
count two parts playing the same note at once, which is common and handled: the
key stays down until the last part lets go, rather than the first.

**A file sounds wrong but the Log looks clean.** Suspect the arrangement rather
than the timing. A handful of pitches repeating hundreds of times in the bass on
a fixed grid is a drum kit that has lost its channel — check the Details tab for
a single track on a single channel, which is what a whole band flattened onto
one piano looks like. Dropping everything below C2 usually removes it: untick
**Move notes outside the range**, and leave the transpose alone, since fitting
would lift the drums up into the tune instead.

**Nothing happens in Roblox.** Status bar bottom-right should say
`uinput: ready`. If not, `setup-uinput.sh` hasn't taken effect — log out and
back in. If it says ready, click directly on the piano so the game has focus.

**Black keys sound a semitone flat.** Modifier dwell too short. Raise it, or
use the 30 fps preset.

**Fast passages lose notes.** Minimum note too short. Raise it.

**Repeated notes only sound once.** Retrigger gap too short. Raise it.

**Notes are wrong by exactly two octaves.** The ctrl-modified octaves of the
88-key layout aren't landing in your game. Try the 61-key layout with **Fit to
keyboard**, or correct those rows in Edit mapping.

**Chords drop notes.** Set **Keys held at once** to 6 or 10 in the Timing tab.

**Everything is roughly right but sounds sloppy.** Work down this list. Play
the test scale first — if the sharps are wrong the timing values are wrong and
nothing else matters. Then check the late count in the Log. Then try the fps
preset matching the frame rate you actually get in game, not the one you
expect. A chord window above about 15ms starts gathering notes that were meant
to be separate, so leave it small.

**A key sticks down.** Press F2. Stop always releases everything, and the
keyboard strip shows every held note so you can see it happen.

**Hotkeys don't work.** They need X11. Mint's Cinnamon session uses X11 by
default; if you've switched to Wayland, use the on-screen buttons.

## Settings

`~/.config/roblox-piano-linux/settings.json`, written on close and read on
startup. Holds the library folder, last file, layout, backend, transpose,
speed, all four timing values, sustain key and cutoff, window position and
hotkeys. Edit it by hand only while the app is closed.

## Uninstalling

```bash
rm -rf /path/to/this/folder
rm ~/.local/share/applications/roblox-piano.desktop
rm -rf ~/.config/roblox-piano-linux        # settings and custom layouts
```

Undo the udev rule:

```bash
sudo rm /etc/udev/rules.d/99-uinput-roblox-piano.rules /etc/modules-load.d/uinput.conf
sudo gpasswd -d "$USER" input
```

## A note on the rules

Autoplayers sit in a grey area with Roblox's terms, and individual games may
take their own view. That's your call; this just handles the Linux side.

## Layout of the code

| File | What's in it |
|------|--------------|
| `rpiano/keycodes.py` | QWERTY character to kernel scancode / X11 keysym |
| `rpiano/layouts.py` | Builds the 61- and 88-key maps, custom layouts, MIDI++ import |
| `rpiano/backends.py` | uinput virtual keyboard, xdotool fallback, dry run |
| `rpiano/midi_loader.py` | Tempo map, absolute timing, track and channel info |
| `rpiano/player.py` | Scheduler, timing model, hold logic, transposition |
| `rpiano/widgets.py` | Keyboard strip, mapping editor |
| `rpiano/gui.py` | Main window |
| `tests_offline.py` | 30 engine tests; no display or MIDI file needed |
| `check_static.py` | AST check for attribute and name typos |

Python 3.10 or newer is required.

```bash
./venv/bin/python tests_offline.py
```

## Licence

MIT. See [LICENSE](LICENSE).
