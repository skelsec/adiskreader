import shlex

class ExtentDescriptor:
    """One extent line of the descriptor file: access, size in sectors, type, [filename, [offset in sectors]]"""
    def __init__(self):
        self.access = None
        self.size_in_sector = None
        self.type = None
        self.filename = None
        self.offset = 0

    @staticmethod
    def from_line(line):
        extent = ExtentDescriptor()
        elems = shlex.split(line)
        if len(elems) < 3:
            raise Exception("Invalid extent descriptor")
        extent.access = elems[0].upper()
        extent.size_in_sector = int(elems[1])
        extent.type = elems[2].upper()
        # ZERO extents have no file
        if len(elems) > 3:
            extent.filename = elems[3]
        if len(elems) > 4:
            extent.offset = int(elems[4])
        return extent
    
    def __str__(self):
        return f"ExtentDescriptor({self.access}, {self.size_in_sector}, {self.type}, {self.filename}, {self.offset})"
