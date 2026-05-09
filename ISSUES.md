# Current Issues / Punch-up

1. get rid of labels in semantic mode
2. read text files in 2000 char mode?
3. height of galaxy cluster map table  - use all space
4. make input box and buttons same for query, ripgrep and network
5. querex errors in live query mode?
6. auto naming of clusters
7. add counter examples in analysis book from downloads

next, there are a few places where we need to be more consistent.

1) input query and buttons for options: used in query, rip grep, and network. i want these views to be more consistent. i want the basic layout to be more like network, with the buttons below the input but not in a box (that is wasted h space). i want the layout to always be separate buttons (like ripgrep and query). The export button is the standard control (ripgrep, network). Options is a standard button and called options (eg not view in query). I want the execute buttons to be separate as in ripgrep , not like the joined button in network.

2) I want the output of papers (the dense/table) (query, rg details, authors) to be consistent too and i want an addtional view: dense and table as current but also a verbose option that includes year, publisher (if any) and hash[:6]. The latter is helpful to find the file - i know we have the link but the name is helpful too. Just the hash is enough to find the file.

pls review the code and give me your plan to implemnt these changes. which files need to change and how (high level)? No editing or code changes until we have agreed the plan. 




# Older Issues

Warning:

archivum [Uber Library] > tag bielecki20(17|18|24) -oa
C:\Users\steve\Documents\CloudStation\TELOS\Python\archivum_project\src\archivum\cli.py:1835: UserWarning: This pattern is interpreted as a regular expression, and has match groups. To actually get the groups, use str.extract.
  mask = lib.ref_df.tag.str.contains(tag_regex, regex=True, na=False, case=False)

***

archivum [Uber Library] > tag Acciaio2011 -vv

--- Full Records for Matches ---
Unexpected error of type <class 'TypeError'>; ignoring.
object of type 'int' has no len()
archivum [Uber Library] >

***

What is the diff between audit and validate

link-tag-hash should move file too...

unknown-unknown-... files (seem to get all three?)
