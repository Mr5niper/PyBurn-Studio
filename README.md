# PyBurn Studio

A professional disc burning application for creating CDs, DVDs, and Blu-ray discs on Linux, macOS, and Windows.

## What Does It Do?

PyBurn Studio lets you:

- **Burn data discs** - Back up your files to CD, DVD, or Blu-ray
- **Create audio CDs** - Make music CDs that play in any CD player
- **Author video DVDs** - Create DVD-Video discs with menus
- **Make Blu-ray discs** - Author BDMV format Blu-ray discs
- **Rip audio CDs** - Extract music to MP3, FLAC, or WAV files

Everything runs through an easy-to-use graphical interface with drag-and-drop support, progress tracking, and automatic verification to ensure your burns are successful.

## Getting Started

### System Requirements

- **Python 3.9 or newer**
- **At least 2GB of RAM** (4GB recommended for video work)
- **An optical drive** (CD/DVD/Blu-ray burner)
- **Free disk space** - About twice the size of what you're burning

### Installation

**Step 1: Install Python packages**

```bash
pip install PyQt6 requests
```

**Step 2: Install system tools**

On **Ubuntu/Debian**:
```bash
sudo apt install genisoimage wodim growisofs cdrdao ffmpeg \
  cdparanoia lame flac dvdauthor isoinfo dvd+rw-tools \
  eject cd-discid xorriso
```

On **macOS** (using Homebrew):
```bash
brew install cdrtools dvd+rw-tools ffmpeg cdrdao cdparanoia \
  lame flac dvdauthor xorriso
```

On **Windows**:
We recommend using WSL2 (Windows Subsystem for Linux) and following the Ubuntu instructions above.

**Step 3: Run the application**

```bash
python pyburn_studio.py
```

That's it! The application will open and you can start burning discs.

## How to Use

### Burning a Data Disc

1. Click the **Data Disc** tab
2. Drag and drop your files and folders into the window
3. Choose your disc type (CD, DVD, or Blu-ray)
4. Set a volume label if you want
5. Click **Queue Job** to start burning

The application will create an ISO image of your files, burn it to the disc, and verify that everything copied correctly.

### Creating an Audio CD

1. Click the **Audio CD** tab
2. Add your music files (MP3, FLAC, WAV, etc.)
3. Use **Move Up/Down** to arrange the track order
4. Optionally add album and track information
5. Click **Queue Job** to create your CD

The CD will play in any standard CD player or car stereo.

### Making a Video DVD

1. Click the **Video DVD** tab
2. Add your video files (MP4, AVI, MKV, etc.)
3. The app will transcode them to DVD format
4. Click **Queue Job** to create your DVD

The resulting DVD will play in standard DVD players.

### Ripping an Audio CD

1. Insert your audio CD
2. Click the **Rip CD** tab
3. Choose your output format (MP3, FLAC, or WAV)
4. Optionally click **Lookup Metadata** to get track names
5. Click **Queue Job** to extract the music

Your ripped tracks will be saved to your Music folder.

## Features Explained

### Job Queue

You can add multiple burn jobs and they'll process one at a time. This is useful if you want to burn several discs in a row without babysitting the computer.

- View all pending jobs in the **Queue** tab
- Cancel the current job if needed
- Remove jobs from the queue before they start

### History and Logs

Every job is logged so you can see what happened:

- The **History** tab shows all completed jobs
- Click **Show Log** to view detailed output
- Click **Retry** to run a previous job again
- Click **Export Log** to save the log file

### Verification

After burning a disc, PyBurn can verify that your data was written correctly:

- **Readback verification** - Reads the disc back and compares checksums
- **Listing verification** - Compares the file list on the disc to your original files

Enable verification in Settings for important burns.

### Smart Features

- **Auto device detection** - Finds your burner automatically
- **Speed selection** - Picks a safe burn speed based on your media
- **RW media handling** - Automatically offers to erase rewritable discs
- **Temp space checking** - Warns you if you don't have enough disk space
- **Capacity gauge** - Shows how much space your files will use

## Keyboard Shortcuts

- **Ctrl+Q** - Quit the application
- **Ctrl+L** - Open the job log window
- **F1** - Show the About dialog

## Settings

Click the **Settings** button to configure:

- **Disc Device** - Which burner to use
- **Burn Speed** - How fast to write (Auto is recommended)
- **Temp Directory** - Where to store temporary files
- **Verify After Burn** - Check disc integrity after writing
- **Auto-blank RW Media** - Automatically erase rewritable discs
- **Eject After Burn** - Pop the disc out when done
- **Simulate When Tools Missing** - Test the app without burning

## Troubleshooting

### The app says "missing tools"

You need to install the system tools listed in the installation section above. If you just want to test the app, enable "Simulate when tools are missing" in Settings.

### My device isn't detected

- Make sure your optical drive is connected and powered on
- Click **Scan** in the Settings dialog
- On Linux, make sure you're in the `cdrom` group: `sudo usermod -a -G cdrom $USER`

### Burns are failing

- Try a slower burn speed (select a specific speed instead of "Auto")
- Use quality blank media from reputable brands
- Clean your drive's lens with a cleaning disc
- Check that your drive firmware is up to date

### The app is slow

- Video transcoding is CPU-intensive and can take time
- Use an SSD for your temp directory if possible
- Close other programs while burning
- For large video files, be patient - transcoding takes time

### Verification failed

- The disc might be damaged or low quality
- Try burning at a slower speed
- Try different blank media
- Your drive may need cleaning

## What Media Can I Use?

- **CD-R** - Write once, 700MB
- **CD-RW** - Rewritable, 700MB
- **DVD-R / DVD+R** - Write once, 4.7GB
- **DVD-RW / DVD+RW** - Rewritable, 4.7GB
- **BD-R** - Blu-ray write once, 25GB
- **BD-RE** - Blu-ray rewritable, 25GB

## Getting Help

- **Logs** - Check `~/.pyburn_logs/` for detailed operation logs
- **Settings file** - Your preferences are in `~/.pyburn_config.json`
- **History file** - Past jobs are recorded in `~/.pyburn_history.json`

## Known Limitations

**Windows Support**  
Windows requires WSL2 or Cygwin to provide Unix-like tools. Native Windows support is limited.

**Blu-ray Authoring**  
Creating Blu-ray discs requires tsMuxeR, which isn't available in most package managers. Download it from the official tsMuxeR website.

**MusicBrainz Lookup**  
Automatic CD metadata lookup requires the `cd-discid` tool and Python `requests` library, plus internet access.

## Technical Details

PyBurn Studio is built with:

- **Python 3.9+** for application logic
- **PyQt6** for the graphical interface
- **Industry-standard tools** for disc operations (mkisofs, cdrecord, growisofs, ffmpeg, etc.)

The application uses a modular architecture with separate layers for the user interface, business logic, and disc operations. Jobs run in background threads so the UI stays responsive.

All operations are logged, and you can run the app in simulation mode to test it without burning actual discs.

## License

GPL-3.0 License

Copyright (c) 2025 [Your Name]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

## Credits

Created with Python and PyQt6. Built on top of excellent open source disc authoring tools maintained by the Linux and BSD communities.
