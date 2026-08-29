# Reproducible README demo, rendered with charmbracelet/vhs:
#   vhs docs/demo/loadout.tape
# Produces docs/demo/loadout.gif. Regenerate whenever the UI changes -- a
# screenshot of a terminal with the desktop showing through is not a demo.

Output docs/demo/loadout.gif

Set Shell bash
Set FontSize 15
Set Width 1180
Set Height 640
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms

Hide
Type "clear" Enter
Show

Type "loadout search fuzz"                             Sleep 400ms  Enter  Sleep 2.5s
Type "loadout show ffuf"                               Sleep 400ms  Enter  Sleep 3.5s
Type "loadout install nuclei subfinder --dry-run"      Sleep 400ms  Enter  Sleep 3.5s
Type "loadout phase lateral-movement"                  Sleep 400ms  Enter  Sleep 3s
Type "loadout sync --dry-run"                          Sleep 400ms  Enter  Sleep 3s
Type "loadout report --since 30d --format markdown"    Sleep 400ms  Enter  Sleep 3s

Sleep 1.5s
