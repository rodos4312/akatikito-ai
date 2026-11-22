"""Ranked local search. Regex evaluation runs in a cancellable isolated process."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SKIP = {".venv","venv","node_modules","__pycache__",".pytest_cache",".git",
        ".agent",".agent_backups","data","dist","build",".mypy_cache"}


def _linked(path):
    try:
        info=path.lstat()
        return path.is_symlink() or bool(getattr(info,"st_file_attributes",0)&0x400)
    except OSError:
        return True


def project_files(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith(".")
                         and not _linked(Path(directory,d)))
        for name in sorted(files):
            path=Path(directory,name)
            if not _linked(path):
                yield path


def scan(root, keyword, max_files=10, regex=False, time_limit=2.0, files=None):
    keyword=str(keyword)
    if not keyword or len(keyword)>4096:
        return {"error":"δώσε μη κενό ερώτημα έως 4096 χαρακτήρες."}
    if regex:
        try:
            pattern=re.compile(keyword,re.IGNORECASE)
        except (re.error,RecursionError) as exc:
            return {"error":f"μη έγκυρο regex '{keyword}': {exc}."}
        matches=lambda text:pattern.search(text) is not None
    else:
        needle=keyword.casefold()
        matches=lambda text:needle in text.casefold()
    definition=re.compile(r"^\s*(?:export\s+|async\s+|public\s+|static\s+)*(?:def|class|function|const|let|var|fn|func|interface|type)\b")
    scored=[];skipped=0;partial=False;started=time.monotonic()
    for path in files if files is not None else project_files(root):
        if time.monotonic()-started>time_limit:
            partial=True;break
        try:
            if not path.is_file():
                continue
            if path.stat().st_size>500_000:
                skipped+=1;continue
            with path.open("rb") as stream:
                raw=stream.read(500_001)
            if len(raw)>500_000:
                skipped+=1;continue
            text=raw.decode("utf-8-sig",errors="replace")
        except OSError:
            skipped+=1;continue
        found=[]
        for number,line in enumerate(text.splitlines(),1):
            if number%32==0 and time.monotonic()-started>time_limit:
                partial=True;break
            if matches(line):
                found.append((number,line))
        relative=path.relative_to(root).as_posix()
        if found:
            defs=[item for item in found if definition.match(item[1])]
            def_lines={item[0] for item in defs}
            score=10*min(len(defs),5)+min(len(found),20)+(8 if matches(relative) else 0)
            shown=sorted((defs+[item for item in found if item[0] not in def_lines])[:5])
            lines=[f"{number:>5} | {line.strip()[:200]}" for number,line in shown]
            scored.append((score,relative,relative+":\n"+"\n".join(lines)))
        if partial:
            break
    scored.sort(key=lambda item:(-item[0],item[1]))
    return {"keyword":keyword,"total":len(scored),"hits":[item[2] for item in scored[:max_files]],
            "skipped":skipped,"partial":partial}


def render(data):
    if data.get("error"):
        return "ΣΦΑΛΜΑ: "+data["error"]
    partial=("\nΜΕΡΙΚΟ ΑΠΟΤΕΛΕΣΜΑ: η αναζήτηση διακόπηκε λόγω χρονικού ορίου ή διακοπής· "
             "δεν εξετάστηκαν όλα τα αρχεία.\n") if data.get("partial") else ""
    if not data.get("hits"):
        return f"Δεν βρήκα '{data.get('keyword','')}' στα αρχεία που εξετάστηκαν."+partial
    hits=data["hits"];total=data["total"]
    head=f"'{data['keyword']}' βρέθηκε σε {total} αρχεία"
    if total>len(hits):
        head+=f" (εμφανίζονται τα {len(hits)} καλύτερα)"
    tail=f"\n... και {data['skipped']} αρχεία που δεν διαβάστηκαν." if data["skipped"] else ""
    return head+":\n"+partial+"\n\n".join(hits)+tail


def search(root,keyword,max_files=10,regex=False,time_limit=2.0,files=None,stopped=lambda:False):
    try:
        limit=float(time_limit)
        if not math.isfinite(limit):
            raise ValueError()
        limit=min(60.0,limit);max_files=max(1,min(50,int(max_files)))
    except (TypeError,ValueError,OverflowError):
        return "ΣΦΑΛΜΑ: μη έγκυρο όριο αναζήτησης."
    if not regex:
        return render(scan(root,keyword,max_files,False,limit,files))
    if not keyword or len(str(keyword))>4096:
        return "ΣΦΑΛΜΑ: δώσε μη κενό regex έως 4096 χαρακτήρες."
    if limit<=0 or stopped():
        return render({"keyword":keyword,"partial":True})
    payload=json.dumps({"root":str(root),"keyword":keyword,"max_files":max_files,
                        "time_limit":max(0,limit*0.8)}).encode("utf-8")
    env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ("KEY","TOKEN","SECRET","PASSWORD"))}
    for key in ("PYTHONHOME","PYTHONPATH","PYTHONSTARTUP"):
        env.pop(key,None)
    kwargs={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    process=None
    try:
        deadline=time.monotonic()+limit
        process=subprocess.Popen([sys.executable,"-I","-S","-B",str(Path(__file__).resolve())],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 cwd=Path(__file__).resolve().parent,env=env,**kwargs)
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0 or stopped():
                process.kill();process.communicate()
                return render({"keyword":keyword,"partial":True})
            try:
                out,err=process.communicate(input=payload,timeout=min(.1,remaining))
                break
            except subprocess.TimeoutExpired:
                payload=None
        if process.returncode or len(out)>100_000:
            return "ΣΦΑΛΜΑ: ο τοπικός έλεγχος regex δεν ολοκληρώθηκε."
        return render(json.loads(out.decode("utf-8")))
    except (OSError,ValueError):
        return "ΣΦΑΛΜΑ: η αναζήτηση regex δεν ήταν διαθέσιμη."
    finally:
        if process is not None and process.poll() is None:
            process.kill();process.communicate()


if __name__=="__main__":
    try:
        args=json.loads(sys.stdin.buffer.read(20000))
        root=Path(args.pop("root"))
        result=scan(root,regex=True,**args)
        # Match snippets are the only file content returned to the parent.
        # Keep a small structured response without dumping every matching file.
        if len(json.dumps(result,ensure_ascii=False).encode("utf-8"))>95000:
            result={"error":"πολύ μεγάλο αποτέλεσμα· περιόρισε το ερώτημα."}
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode("utf-8"))
    except Exception:
        sys.stdout.buffer.write(b'{"error":"regex worker failed"}')
