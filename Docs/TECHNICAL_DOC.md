# PyBurn Studio - Technical Documentation
Version 1.7.2

## OVERVIEW

PyBurn Studio is a disc burning application written in Python with a Qt GUI.
It wraps common Linux disc authoring tools (mkisofs, cdrecord, growisofs, etc.)
and provides a user-friendly interface for burning CDs, DVDs, and Blu-rays.

Think of it as a frontend - we don't reinvent burning, we just make the 
existing tools easier to use.

## HOW IT WORKS (THE BIG PICTURE)

When you burn a disc, here's what happens:

1. You pick files and click "Queue Job"
2. Job goes into a queue (one at a time to avoid conflicts)
3. A background worker thread starts
4. Worker calls external tools (mkisofs, cdrecord, etc.)
5. We parse their output for progress updates
6. When done, we optionally verify the disc
7. Results saved to history

That's it. We're basically a smart wrapper around command-line tools.

## FILE STRUCTURE

The code is split into logical sections:

**CORE (pyburn/core/):**
Basic building blocks that don't do much on their own.
- config.py - Reads/writes ~/.pyburn_config.json
- tools.py - Finds external programs (mkisofs, cdrecord, etc.)
- devices.py - Detects CD/DVD drives
- jobs.py - Data structure for a burn job
- history.py - Reads/writes ~/.pyburn_history.json

**SERVICES (pyburn/services/):**
The actual work happens here.
- exec.py - Runs external commands, captures output
- backend.py - Does the real burning (calls mkisofs, cdrecord, etc.)
- queue.py - Manages the job queue
- progress.py - Parses tool output for progress (looks for "45% done")
- media.py - Checks what disc is in the drive
- verify.py - Reads disc back to verify it burned correctly
- metadata.py - Looks up CD info from MusicBrainz

**GUI (pyburn/gui/):**
Everything you see on screen.
- main_window.py - Main app window
- tabs.py - The 5 tabs (Data, Audio, DVD, Blu-ray, Rip)
- widgets.py - Custom controls (file list, progress gauge)
- dialogs.py - Settings window, log viewer
- style.py - Dark theme colors

## THE BURNING PROCESS (STEP BY STEP)

Let's walk through burning a data disc:

**STEP 1: User Interaction**
User drags files into the Data tab, clicks "Queue Job: Burn Data Disc"

**STEP 2: Job Creation**
GUI creates a Job object with:
- job_type = "DATA"
- files = [list of paths]
- device = "/dev/sr0"
- options = speed, verify, volume label, etc.

**STEP 3: Enqueue**
Job goes into JobQueueService.
If no job is running, it starts immediately.
Otherwise it waits in line.

**STEP 4: Worker Thread Starts**
JobQueueService creates a BurnWorker in a new QThread.
This keeps the UI responsive while burning.

**STEP 5: Backend Selection**
Worker checks if required tools exist (mkisofs, cdrecord).
If yes: use RealBackend (actually burns)
If no: use SimulatedBackend (fake progress for testing)

**STEP 6: ISO Creation**
RealBackend runs: mkisofs -o temp.iso -J -R -V "LABEL" /your/files
We monitor the temp.iso file size to show progress.

**STEP 7: Burning**
If we have growisofs: growisofs -Z /dev/sr0=temp.iso
If not: cdrecord dev=/dev/sr0 speed=8 temp.iso
We parse their output for progress updates.

**STEP 8: Verification (Optional)**
If verify is enabled:
- Try readom to read disc back (compare checksums)
- If that fails, use isoinfo to list files and compare

**STEP 9: Cleanup**
Delete temp.iso, eject disc, mark job complete.
Save results to history.

## THREADING MODEL (WHY THINGS DON'T FREEZE)

The app uses 3 types of threads:

**MAIN THREAD (UI):**
Handles all Qt widgets, never blocks.
Gets updates via Qt signals (thread-safe messaging).

**JOB THREAD:**
One per burn job.
Lives in a QThread.
Calls external tools, emits progress signals.
Dies when job completes.

**PUMP THREADS:**
Two per external tool (one for stdout, one for stderr).
Read output line-by-line, call callbacks.
Daemon threads so they die with parent.

Why this design?
- UI never freezes because work is in other threads
- Qt signals let threads talk safely
- One job at a time prevents device conflicts

## VERIFICATION (HOW WE CHECK BURNS WORKED)

We have a two-level system:

**LEVEL 1 - Readback:**
Use readom to read the disc back into a file.
Compare file size to original ISO.
If under 100MB, compute SHA-256 checksums.
This is the gold standard but requires readom tool.

**LEVEL 2 - Listing Compare:**
Use isoinfo to list all files on disc.
Use isoinfo to list all files in ISO.
Compare the two lists.
If disc is missing files, verification fails.
This doesn't require mounting the disc (no root needed).

Why two levels?
Readback is best but not always available.
Listing compare is a good fallback.

## PROGRESS TRACKING (HOW WE SHOW PERCENTAGES)

Different tools report progress differently:

**GROWISOFS:**
Prints "45.3% done" to stdout.
We regex match that and update progress bar.

**CDRECORD:**
Prints "Track 01:  67% done" to stderr.
Sometimes prints "fifo 89%" for buffer.
We parse both patterns.

**FFMPEG:**
Doesn't give percentages directly.
We break video encoding into phases (33%, 66%, 100%).
Not frame-accurate but gives user feedback.

**CDPARANOIA:**
Prints "[  45% ]" with progress.
We extract the number.

**FILE GROWTH:**
For ISO creation, we monitor the output file size.
Progress = current_size / expected_size * 100

## ERROR HANDLING

Things that can go wrong:

**MISSING TOOLS:**
If tools aren't installed, we use SimulatedBackend.
Shows fake progress so you can test the UI.
You can disable this in Settings.

**DEVICE NOT FOUND:**
We scan for devices on startup.
If none found, use platform defaults (/dev/sr0, etc.).
User can rescan in Settings.

**BURN FAILURE:**
If cdrecord exits with error code, we catch it.
Log goes to ~/.pyburn_logs/JOBID.log
User can view log from History tab.

**OUT OF SPACE:**
We check temp directory before starting.
If not enough space, warn user.
Data needs 1.2x, video needs 2.5x source size.

**CANCELLATION:**
User can click "Cancel Current" in Queue tab.
We send SIGTERM to process.
If it doesn't die in 2 seconds, send SIGKILL.
Temp files cleaned up in finally block.

## CONFIGURATION

Settings stored in ~/.pyburn_config.json as JSON with these fields:

- burn_speed: "Auto" or number like 8
- verify_after_burn: true/false - verify discs?
- temp_dir: "/home/user/PyBurn_Temp"
- default_device: "/dev/sr0"
- simulate_when_missing_tools: true/false
- auto_blank_rw: true/false - blank RW discs automatically?
- eject_after_burn: true/false
- musicbrainz_enabled: true/false

History in ~/.pyburn_history.json:
List of completed jobs with success/failure, timestamps, log paths.

Logs in ~/.pyburn_logs/:
One file per job with complete output from all tools.

## EXTERNAL TOOL DEPENDENCIES

We need these Linux command-line tools:

**DATA BURNING:**
- mkisofs or genisoimage (makes ISO files)
- cdrecord or wodim (burns CDs)
- growisofs (burns DVDs/Blu-rays)

**AUDIO:**
- ffmpeg (converts audio formats)
- cdrdao (burns audio CDs with CD-Text)
- lame (MP3 encoding)
- flac (FLAC encoding)

**VIDEO:**
- ffmpeg (video transcoding)
- dvdauthor (DVD structure)
- tsMuxeR (Blu-ray structure)

**VERIFICATION:**
- isoinfo (list ISO contents)
- readom or readcd (read disc back)

**MEDIA INFO:**
- dvd+rw-mediainfo (check disc type/speed)
- dvd+rw-format (blank RW discs)
- eject (eject discs)

**METADATA:**
- cd-discid (get CD ID for MusicBrainz)
- Python requests library (HTTP for MusicBrainz)

## PLATFORM DIFFERENCES

**LINUX:**
Everything works. This is the primary platform.
Device paths like /dev/sr0, /dev/cdrom

**MACOS:**
Most tools available via Homebrew.
Device paths like /dev/disk2
Some tools have different behavior.

**WINDOWS:**
Very limited support.
Most tools don't exist natively.
Best option: Use WSL2 (Windows Subsystem for Linux)
Alternative: Cygwin or MSYS2 for Unix tools

## COMMON CUSTOMIZATION POINTS

**Want to add a new burn type?**
1. Add enum to JobType in jobs.py
2. Add tab in tabs.py
3. Add backend method in backend.py
4. Add tool requirements in burn.py

**Want to support a new tool?**
1. Add to TOOL_CANDIDATES in tools.py
2. Write progress parser in progress.py if needed
3. Use in backend.py

**Want to change the theme?**
Edit APP_STYLESHEET in gui/style.py

**Want to add a new verification method?**
Add method to VerificationTools in verify.py
Call from verify() main method

## TESTING

**SELF-TEST:**
Run: python pyburn_studio.py --self-test
This queues 5 simulated jobs and verifies queue works.
Takes about 30 seconds.
No hardware or tools required.

**MANUAL TESTING:**
1. Enable simulation mode in Settings
2. Try each tab (Data, Audio, DVD, Blu-ray, Rip)
3. Watch progress bars and status messages
4. Check History tab shows completed jobs
5. Try canceling a job mid-burn

**REAL BURN TESTING:**
1. Insert blank disc
2. Disable simulation mode
3. Burn small test project
4. Enable verification
5. Check disc in another computer

## PERFORMANCE NOTES

**ISO CREATION:**
Speed depends on disk I/O.
SSD: ~50 MB/s
HDD: ~20 MB/s

**BURNING:**
Speed depends on drive and media.
8x DVD = ~11 MB/s
16x CD = ~2.4 MB/s

**VIDEO TRANSCODING:**
CPU-bound, can take 30+ minutes for large files.
Real-time encoding: 1 hour video takes ~1 hour.
Faster CPU = faster encoding.

**VERIFICATION:**
Readback limited by optical drive read speed.
Typically 8x-16x max even if drive can burn faster.

## TROUBLESHOOTING GUIDE

**"No devices found":**
- Check drive is connected and powered
- Run "Device Scan" in Settings
- Linux: check /dev/sr0 exists
- Linux: add user to cdrom group

**"Missing tools warning":**
- Install tools for your platform
- Or enable simulation mode for testing

**"Burn succeeds but verification fails":**
- Try lower burn speed (8x for DVD, 16x for CD)
- Use quality brand-name media
- Clean drive lens
- Check drive isn't overheating

**"Video transcoding very slow":**
- Normal for large files
- Close other apps to free CPU
- Use faster preset in future (we use veryfast already)

**"Out of space" error:**
- Free up temp directory
- Or change temp dir to larger drive in Settings
- Video needs 2.5x source size for temp files

**"App freezes":**
- Shouldn't happen (everything is threaded)
- If it does, it's a bug - report it
- Try Ctrl+C in terminal to see traceback

## SECURITY CONSIDERATIONS

**COMMAND INJECTION:**
We use subprocess with list arguments (not shell=True).
User input goes in args array, not concatenated strings.
Safe from shell injection.

**FILE ACCESS:**
User controls what files get burned.
We don't restrict this - user responsibility.
Temp files created in user's home directory.

**NETWORK:**
Only used for optional MusicBrainz lookups.
User can disable in Settings.
No telemetry or auto-updates.

**PRIVILEGES:**
App runs as regular user, no root needed.
Some tools may need user in 'cdrom' group on Linux.

## KNOWN LIMITATIONS

- Progress during video transcoding is phase-based, not frame-accurate
- Windows support requires WSL or Unix tool ports
- Blu-ray requires tsMuxeR (not in most package managers)
- Dual-layer media not explicitly tested
- No support for disc-to-disc copying (copyright reasons)
- No support for protected content (DVDs with CSS)

## END OF TECHNICAL DOCUMENTATION