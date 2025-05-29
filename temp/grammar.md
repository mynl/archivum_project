GRAMMAR
=======

clause_list         	::= 
                    	 | clause_list clause
                    	 | clause

clause              	::= TOP NUMBER
                    	 | flags
                    	 | regexes
                    	 | SELECT select_list
                    	 | WHERE where_clause_expression
                    	 | ORDER_BY column_sort_list

flags               	::= FLAG flags
                    	 | FLAG

regexes             	::= regexes AND regex
                    	 | regex

regex               	::= IDENTIFIER TILDE REGEX_SLASHED
                    	 | IDENTIFIER TILDE IDENTIFIER
                    	 | BANG REGEX_SLASHED
                    	 | BANG IDENTIFIER

select_list         	::= select_list "," select_item
                    	 | select_item

select_item         	::= IDENTIFIER
                    	 | NOT IDENTIFIER
                    	 | STAR

where_clause_expression	::= where_clause_expression AND where_clause
                    	 | where_clause

where_clause        	::= IDENTIFIER EQ_TEST QUOTED_STRING
                    	 | IDENTIFIER EQ_TEST IDENTIFIER
                    	 | IDENTIFIER EQ_TEST NUMBER
                    	 | IDENTIFIER EQ_TEST DATETIME

column_sort_list    	::= column_sort_list "," column_sort
                    	 | column_sort

column_sort         	::= IDENTIFIER
                    	 | NOT IDENTIFIER



TOKENS
======
        IDENTIFIER, NUMBER, QUOTED_STRING, REGEX_SLASHED,
        DATETIME, SELECT, ORDER_BY, WHERE, TOP, EQ_TEST, AND,
        FLAG, STAR, NOT, DATETIME, TILDE, BANG

DEFINITIONS
===========
    SELECT = 'select|SELECT'
    ORDER_BY = 'order|ORDER|sort|SORT'
    WHERE = 'where|WHERE'
    TOP = 'top|TOP'
    AND = 'and|AND'    # just AND, otherwise into parens, order of ops etc.
    FLAG = 'recent|RECENT|verbose|VERBOSE'

    STAR = r'\*'
    TILDE = r'~'
    BANG = r'!'
    # to reverse sort order
    NOT = r'\-'
    EQ_TEST = r'==|<=|<|>|>='
    DATETIME complex regex
    NUMBER = r'-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf'
    QUOTED_STRING = r'''"([^"\]|\.)*"|'([^'\]|\.)*''''
    IDENTIFIER = r'[a-z_][a-z0-9_]*'
    REGEX_SLASHED = r'/([^/\]|\.)*/'
