# Fleet rules for the documentation example

Create only the exact files in your ticket scope under `docs/fleet-demo/`. Preserve any other files. Write concise Markdown. No external research, installs, servers or build commands are needed. No glossary or ADR exists for this disposable example beyond the sample tickets and the agreed spec in `examples/README.md`.

Run only the check corresponding to your own scope, from the repository root. The lead also reads the prose against all acceptance criteria; existence alone is insufficient evidence.

Overview:
```sh
python3 -c 'from pathlib import Path; p=Path("docs/fleet-demo/overview.md"); s=p.read_text(); assert s.startswith("# ") and "lead" in s.lower() and "worker" in s.lower()'
```

Glossary:
```sh
python3 -c 'from pathlib import Path; s=Path("docs/fleet-demo/glossary.md").read_text().lower(); assert all(w in s for w in ("lead", "worker", "ticket", "verified"))'
```

Guide:
```sh
python3 -c 'from pathlib import Path; p=Path("docs/fleet-demo/guide.md"); s=p.read_text(); assert all("("+f+")" in s and (p.parent/f).is_file() for f in ("overview.md", "glossary.md"))'
```

Combined check (lead only, after every ticket is complete):
```sh
python3 -c 'from pathlib import Path; import re; d=Path("docs/fleet-demo"); assert all((d/n).is_file() for n in ("overview.md", "glossary.md", "guide.md")); links=re.findall(r"\]\(([^)]+)\)", (d/"guide.md").read_text()); assert links and all((d/link).is_file() for link in links)'
```
