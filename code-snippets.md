# Scripting Code Snippets


```bash
# full monty import
cd /s/telos/python/archivum_project/new-papers
import-bibtex -x -v -p . "library - Copy.bib"
```



```bash
# single import
cd /tmp
import-bibtex -vv  tweedie.bib
# actually import it
import-bibtex -v -x  tweedie.bib
```


## Import docs

```bash
# discover / import
cd /tmp
archivum uber -l silly-library2
import-doc -v .
import-bibtex -v  bibtex-import.bib
# actually import it
import-bibtex -v -x  tweedie.bib
```
