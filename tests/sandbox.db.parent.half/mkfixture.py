"""Create K checkpointed test boards + the Test Student in SANDBOX course 198177 ONLY."""
import sys,json,argparse
sys.path.insert(0,'/home/mikeg/Repositories/Canvas.API/src')
import canvas_course, urllib.request
ap=argparse.ArgumentParser(); canvas_course.add_arguments(ap); ap.add_argument('--k',type=int,default=6); ap.add_argument('--start',type=int,default=1); a=ap.parse_args(sys.argv[1:])
cfg,BASE,TOK=canvas_course.setup(a)
assert str(cfg.course_id)=="198177", "refusing: not the sandbox course"
def call(p,m="GET",d=None,js=None):
    h={"Authorization":"Bearer "+TOK}; body=None
    if js is not None: body=json.dumps(js).encode(); h["Content-Type"]="application/json"
    r=urllib.request.Request(BASE+p,data=body if js is not None else d,method=m,headers=h)
    with urllib.request.urlopen(r) as x: return json.load(x)
#ts=call("/api/v1/courses/198177/student_view_student","POST",b""); print('test student',ts['id'],ts.get('name'))
Q='''mutation($in: CreateDiscussionTopicInput!){ createDiscussionTopic(input:$in){ discussionTopic{ _id title assignment{ _id hasSubAssignments } } errors{ message } } }'''
out=[]
for i in range(a.k):
    inp={"contextId":"198177","contextType":"Course","title":"VERIFY-TEST board %02d"%(i+a.start),"discussionType":"threaded","published":True,
         "assignment":{"courseId":"198177","name":"VERIFY-TEST board %02d"%(i+a.start),"gradingType":"points","forCheckpoints":True},
         "checkpoints":[{"checkpointLabel":"reply_to_topic","pointsPossible":4.0,"repliesRequired":1,"dates":[{"type":"everyone","dueAt":"2031-01-10T04:59:00Z"}]},
                        {"checkpointLabel":"reply_to_entry","pointsPossible":4.0,"repliesRequired":2,"dates":[{"type":"everyone","dueAt":"2031-01-12T04:59:00Z"}]}]}
    r=call("/api/graphql","POST",js={"query":Q,"variables":{"in":inp}})
    print(json.dumps(r)[:400]); out.append(r)
json.dump(out,open(sys.argv[0]+'.created.%d.json'%a.start,'w'))
