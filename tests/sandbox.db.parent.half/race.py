import sys,json,argparse,time,concurrent.futures as cf
sys.path.insert(0,'/home/mikeg/Repositories/Canvas.API/src')
import canvas_course, db_grades_verify as V
ap=argparse.ArgumentParser(); canvas_course.add_arguments(ap); ap.add_argument("--rounds",type=int,default=8); ap.add_argument("--conc-only",action="store_true"); ap.add_argument('--workers',type=int,default=6); a=ap.parse_args(sys.argv[1:])
cfg,BASE,TOK=canvas_course.setup(a); assert str(cfg.course_id)=="198177"
AIDS=[2957211,2957214,2957217,2957220,2957223,2957226]; UID=500625
def put(p):
    aid,tag,val=p
    try:
        with V._open(BASE,TOK,"/api/v1/courses/198177/assignments/%s/submissions/%s"%(aid,UID),"PUT",{"submission[posted_grade]":val,"sub_assignment_tag":tag}): pass
        return None
    except Exception as e: return str(e)
def round_(r,workers):
    t=(r%3)+1; e=(r%3)+2
    plan=[(aid,tag,val) for aid in AIDS for tag,val in ((V.TOPIC,t),(V.ENTRY,e))]
    with cf.ThreadPoolExecutor(max_workers=workers) as ex: errs=[x for x in ex.map(put,plan) if x]
    time.sleep(1)
    found,rerr=V.verify(BASE,TOK,198177,{"B%d"%i:aid for i,aid in enumerate(AIDS)})
    return errs,found
MODES=(("concurrent x%d"%a.workers,a.workers),) if "--conc-only" in sys.argv else (("concurrent x%d"%a.workers,a.workers),("serial x1",1))
for label,w in MODES:
    bad=0;cells=0;errs=0
    for r in range(a.rounds):
        e,f=round_(r+ (0 if w>1 else 100),w); errs+=len(e); bad+=len(f); cells+=len(AIDS)
        if f: print('  ',label,'round',r,[(m['board'],m['parent'],m['halves']) for m in f][:3])
    print(label,': mismatches',bad,'of',cells,'board-cells; put errors',errs)
