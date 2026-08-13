# Documentation

Edit `user_guide.md` as the source file.

For live browser preview, run from the repository root:

```powershell
python -B app\docs\preview_user_guide.py
```

Then open:

```text
http://127.0.0.1:8765/user_guide.html
```

The preview rebuilds `user_guide.html` when you save the Markdown. The browser
checks for a saved change in the background and reloads the page only after a
new build is available.
