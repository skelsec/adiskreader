import gzip
import os


for root, dirs, files in os.walk('./testfiles/'):
    for file in files:
        if file.endswith('.gz') is True:
            continue
        filepath = os.path.join(root, file)
        with gzip.open(filepath+'.gz', 'wb') as f:
            f.write(open(filepath, 'rb').read())