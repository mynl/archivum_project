

def generate_pdf_path(n: int, library):
    refs = library.ref_df
    rd = library.ref_doc_df
    tag = refs.iloc[n].tag
    docs = rd.query('tag == @tag')
    if len(docs) == 1:
        return docs.iloc[0].path
    elif len(docs) > 1:
        print('MORE THAN ONE FILE')
        return docs.iloc[0].path
    else:
        return None
