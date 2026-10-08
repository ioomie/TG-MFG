# Upload to GitHub

Target repository: [ioomie/TG-MFG](https://github.com/ioomie/TG-MFG).

Use the clean source directory only. Review `git status` and `.gitignore`; do not upload the parent workspace or private development/runtime directories. ZIPs go in Releases after real-machine checks, not in the source commit.

If this local repository still has **no local commits** and GitHub already has its initial README/license commit:

```sh
# Run inside this project's source directory.
git remote add origin https://github.com/ioomie/TG-MFG.git
git fetch origin main
# Adopt the remote initial history while keeping all prepared working files.
git reset --mixed origin/main
git add .
git diff --cached --check
git diff --cached --stat
git commit -m "Add TG-MFG core, bilingual interface and documentation"
git push -u origin main
```

Use `git remote set-url origin ...` if origin already exists. The reset recipe is only for this first upload with no local commits; if you already have commits, reconcile history separately. Never use `--hard` or force-push for this procedure.

Authenticate through your normal Git credential manager, SSH key or `gh auth login`. Never put a token in a command, remote URL, README or committed file. In a graphical Git client, select this exact source folder and review the same staged changes before committing/pushing.

The English README is default. Link to `README.zh-CN.md` for Chinese. Suggested repository description:

> A local Telegram channel explorer with reaction ranking, keyword and hashtag filters, a blocked box, and optional local snapshots. macOS and Windows only. Entirely AI-generated.

For the repository social preview, upload `docs/assets/tg-mfg-social.png` in Settings. Enable private vulnerability reporting under Security. GitHub Actions will run after the first code push.
