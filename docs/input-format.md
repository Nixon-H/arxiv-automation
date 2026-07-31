# Input Format

`endorsers.txt` (default) — blank-line-separated blocks:

```
Dr. Alice Smith is qualified to endorse.
Paper Title Here
alice.smith@example.com

Prof. Bob Johnson is qualified to endorse.
Another Paper Title
bob@example.com
```

Line 1: name + "is/are qualified" (title auto-detected: Dr./Prof./Mr./Ms./Miss/Mrs.)
Line 2: paper title
Line 3: email

Also supported: `.csv`, `.json`, `.yaml`, `.xlsx` (auto-detected by extension).
