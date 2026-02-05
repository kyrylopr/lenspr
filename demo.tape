Output assets/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 900
Set Height 520
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 35ms

Hide
Type "source /Users/kyryloprymak/code/lenspr/assets/demo-setup.sh"
Enter
Sleep 1500ms
Show

# --- Init: parse codebase into graph ---
Type "lenspr init ."
Enter
Sleep 2s

# --- Feature 1: One call to understand any function ---
Type ""
Enter
Type "# Instead of grep → read → grep → read..."
Enter
Sleep 1.5s
Type "lenspr context validate_email"
Enter
Sleep 3.5s

# --- Feature 2: Impact analysis before every change ---
Type ""
Enter
Type "# Before changing anything:"
Enter
Sleep 1s
Type "lenspr impact validate_email"
Enter
Sleep 3.5s

# --- Closing: ties to README tagline ---
Type ""
Enter
Type "# One call. Full context. No more blind edits."
Enter
Sleep 2.5s
