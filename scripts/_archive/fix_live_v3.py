# fix_live_v3.py - remove -t flag from psql command
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\routes\marketdata.py"

with open(path, encoding='utf-8') as f:
    content = f.read()

# Remove -t from the command (it suppresses headers, breaking CSV parsing)
old = "'--no-psqlrc','-t','-A','--csv','-c',sql]"
new = "'--no-psqlrc','-A','--csv','-c',sql]"

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("OK - removed -t flag")
else:
    print("Pattern not found, current state:")
    idx = content.find("--no-psqlrc")
    print(repr(content[idx:idx+100]))
