# herdr-park demo: hibernate idle Claude tabs, watch the memory drop, resume one from the picker.
Output demo.mp4
Output demo.gif

Set Shell "zsh"
Set FontSize 15
Set Width 1280
Set Height 720
Set Padding 12
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set CursorBlink false

Hide
Type "herdr session attach demo"
Enter
Sleep 4s
Show

Sleep 1s
Type "# 3 idle Claude tabs holding memory"
Sleep 300ms
Enter
Sleep 500ms
Type "herdr-park hibernate-idle -y"
Sleep 500ms
Enter
Sleep 6s

Ctrl+B
Type "u"
Sleep 1.5s
Type "rate"
Sleep 1s
Enter
Sleep 5s
