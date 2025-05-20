# Port Mendeley

--> see Joplin

One-time steps to port existing Mendeley libary into archivum. No attempt to be generic!

Last added file is Mark Browne NAAJ 2025.

## Steps

1. Bibtex to dataframe
2. Normalize author names
    * De-TeX
    * Initials
3. Normalize journals: de-TeX
4. Normalize publishers: de-TeX
5. Validate linked files / fuzzy look up
6. Normalize tags
6. Update dataframe
7. Recreate Bib file with selected fields
8. Create qmd file tester file with all tags
