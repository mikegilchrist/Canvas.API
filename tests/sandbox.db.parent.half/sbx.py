import sys,json,argparse,urllib.request,urllib.parse
sys.path.insert(0,'/home/mikeg/Repositories/Canvas.API/src')
import canvas_course
ap=argparse.ArgumentParser(); canvas_course.add_arguments(ap); ap.add_argument('op'); a=ap.parse_args(sys.argv[1:])
cfg,BASE,TOK=canvas_course.setup(a); assert str(cfg.course_id)=="198177"
def call(p,m="GET",d=None):
    r=urllib.request.Request(BASE+p,data=urllib.parse.urlencode(d).encode() if d is not None else None,method=m,headers={"Authorization":"Bearer "+TOK})
    with urllib.request.urlopen(r) as x: return json.load(x)
if a.op=='publish': print(call("/api/v1/courses/198177","PUT",{"course[event]":"offer"}).get('workflow_state'))
if a.op=='unpublish': print(call("/api/v1/courses/198177","PUT",{"course[event]":"claim"}).get('workflow_state'))
if a.op=='student': t=call("/api/v1/courses/198177/student_view_student","POST",{}); print(t['id'],t.get('name'))
