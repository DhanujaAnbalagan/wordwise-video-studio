import subprocess, json, wave, numpy as np, os, re, sys
ESPEAK="/nix/store/x10vak7vdhq4737mxwqvlplrg982mr56-espeak-ng-1.52.0.1-unstable-2025-09-09/bin/espeak-ng"
SR=22050
cache={}
def synth(word, voice):
    key=(word.lower(),voice)
    if key in cache: return cache[key]
    clean=re.sub(r"[^\w'’-]","",word)
    p="/tmp/media/w.wav"
    subprocess.run([ESPEAK,"-v",voice,"-s","165","-w",p,clean],check=True)
    with wave.open(p) as w:
        assert w.getframerate()==SR, w.getframerate()
        a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)
    thr=300
    idx=np.where(np.abs(a)>thr)[0]
    a=a[max(0,idx[0]-80):idx[-1]+80] if len(idx) else a
    cache[key]=a
    return a

def build(script, out, speakers_meta, title, sub, confidences=None):
    # script: list of items: ("S1", "sentence text") or ("pause", seconds) or ("at", seconds) or ("overlap", seconds)
    voices={"S1":"en-us","S2":"en-us+f3"}
    audio=[]; t=0.0; words=[]; n=0
    def add_silence(s):
        nonlocal t
        audio.append(np.zeros(int(round(s*SR)),dtype=np.float32)); t+=s
    add_silence(0.6)
    for item in script:
        kind=item[0]
        if kind=="pause": add_silence(item[1]); continue
        if kind=="at":
            assert t<=item[1], (t,item)
            add_silence(item[1]-t); continue
        spk,text=item
        toks=text.split()
        for i,tok in enumerate(toks):
            a=synth(tok,voices[spk])
            st=t; audio.append(a); t+=len(a)/SR
            n+=1
            wd={"word_id":f"w_{n:04d}","text":tok,"start":round(st,3),"end":round(t,3),"speaker_id":spk}
            words.append(wd)
            gap=0.07
            if tok[-1] in ".?!": gap=0.45
            elif tok[-1] in ",;:": gap=0.22
            if i<len(toks)-1 or True: add_silence(gap)
    add_silence(1.0)
    full=np.concatenate(audio)
    full=np.clip(full,-32767,32767).astype(np.int16)
    with wave.open(f"/tmp/media/{out}.wav","wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(full.tobytes())
    dur=len(full)/SR
    font="/usr/share/fonts/noto/NotoSans.ttf"
    vf=(f"drawbox=x=0:y=0:w=iw:h=ih:color=0x14181f:t=fill,"
        f"drawbox=x=40:y=300:w='(iw-80)*t/{dur:.3f}':h=4:color=0x2dd4bf:t=fill,"
        f"drawtext=fontfile={font}:text='{title}':x=40:y=48:fontsize=30:fontcolor=0xe5e7eb,"
        f"drawtext=fontfile={font}:text='{sub}':x=40:y=92:fontsize=18:fontcolor=0x60a5fa,"
        f"drawtext=fontfile={font}:text='SOURCE TIME':x=40:y=170:fontsize=14:fontcolor=0x9ca3af,"
        f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':x=40:y=192:fontsize=56:fontcolor=0xf3f4f6")
    subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"color=c=black:s=640x360:r=24:d={dur:.3f}",
        "-i",f"/tmp/media/{out}.wav","-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","32","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","64k","-ar","44100","-shortest","-movflags","+faststart",f"/tmp/media/{out}.mp4"],check=True)
    if confidences:
        for w in words:
            w["confidence"]=confidences.get(w["word_id"],0.97)
    tr={"source":{"provider":"fixture","model":"espeak-ng 1.52 scripted synthesis","note":"Word timestamps are exact: audio was synthesised word-by-word from this script."},
        "duration":round(dur,3),"speakers":speakers_meta,"words":words}
    json.dump(tr,open(f"/tmp/media/{out}.json","w"),indent=1)
    return words,dur

lecture=[
 ("S1","Hello everyone, and welcome."),
 ("S1","Before we start, a quick housekeeping note about the room and the recording."),
 ("pause",2.6),
 ("S1","Um, okay, let us begin with the introduction to this course."),
 ("S1","Today we study how software companies decide on pricing for their products."),
 ("S1","I like this topic, because every founder struggles with it."),
 ("at",42.0),
 ("S1","Welcome to lesson one, new students."),
 ("S1","Uh, the first idea is value."),
 ("S1","Customers pay for outcomes, not for features."),
 ("S1","When we talk about pricing, we really talk about perceived value."),
 ("pause",1.1),
 ("S1","The second idea is cost."),
 ("S1","Your cost to serve each customer sets a floor, never a ceiling."),
 ("S1","Some of these numbers are confidential, so please do not share the slides."),
 ("pause",3.2),
 ("S1","Now, um, let us compare three plans."),
 ("S1","A starter plan, a team plan, and an enterprise plan."),
 ("S1","Good pricing makes the middle plan the obvious choice."),
 ("S1","That is the end of part one. Thank you."),
]
w,d=build(lecture,"lecture-pricing",{"S1":{"label":"Lecturer"}},"Pricing Strategy 101","Synthetic lecture  /  single speaker")
print("lecture",d,len(w)); print([ (x["text"],x["start"]) for x in w if 40<x["start"]<47 or "pric" in x["text"].lower()])
interview=[
 ("S1","So, welcome to the show."),
 ("S1","Today my guest built a small video startup."),
 ("S2","So, uh, thanks for having me."),
 ("S2","Happy to be here."),
 ("S1","Um, tell us how you found your first customers."),
 ("S2","Honestly, we just, um, emailed one hundred teachers."),
 ("S2","Twelve replied, and three paid."),
 ("pause",1.8),
 ("S1","What did you charge them?"),
 ("S2","Pricing was nine dollars a month at first."),
 ("S1","And, uh, what would you change today?"),
 ("S2","I would raise the price sooner."),
 ("S1","Great advice. Thank you."),
]
conf={}
w2,d2=build(interview,"interview-founder",{"S1":{"label":"Host"},"S2":{"label":"Guest"}},"Founder Interview","Synthetic interview  /  two speakers")
# hand-authored confidence fixture to exercise low-confidence warnings
lowc={}
for x in w2:
    t=x["text"].lower().strip(".,?!")
    if t in ("twelve","emailed"): lowc[x["word_id"]]=0.41 if t=="twelve" else 0.58
tr=json.load(open("/tmp/media/interview-founder.json"))
for x in tr["words"]: x["confidence"]=lowc.get(x["word_id"],0.96)
tr["source"]["note"]+=" Confidence values are hand-authored test values (not produced by an ASR model) to exercise low-confidence warnings."
json.dump(tr,open("/tmp/media/interview-founder.json","w"),indent=1)
print("interview",d2,len(w2))
