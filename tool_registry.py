"""Deprecated static registry.

Tool schemas are now discovered dynamically from MCP via session.list_tools().
This module remains only for compatibility with older callers.
"""


TOOLS = [

    {
        "name":
        "list_directory",

        "description":
        "List files inside workspace",

        "parameters":
        {
            "path":
            {
                "type":
                "string"
            }
        }
    },


    {
        "name":
        "read_text",

        "description":
        "Read text file",

        "parameters":
        {
            "path":
            {
                "type":
                "string"
            }
        }
    },


    {
        "name":
        "replace_text",

        "description":
        "Replace exact text in file",

        "parameters":
        {
            "path":
            {
                "type":
                "string"
            },

            "old":
            {
                "type":
                "string"
            },

            "new":
            {
                "type":
                "string"
            }
        }
    },


    {
        "name":
        "run_process",

        "description":
        "Run allowed local program",

        "parameters":
        {
            "program":
            {
                "type":
                "string"
            },

            "args":
            {
                "type":
                "array"
            }
        }
    }
]


def get_tools():
    return TOOLS
