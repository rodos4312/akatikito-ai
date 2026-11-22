"""Owner task state and completion evidence, separate from long-term memory."""
from __future__ import annotations
from .intent import normal, positive_actions, resolve_selection, classify


class TaskState:
    def __init__(self):
        self.clear()

    def clear(self):
        self.goal = ''
        self.status = 'idle'
        self.kind = 'ask'
        self.parent_goal = ''
        self.todos = []

    def begin(self, text, messages):
        goal, selected = resolve_selection(text, messages)
        value = normal(text).strip(' .!;')
        continuation = value in ('συνεχισε','προχωρα','ok','οκ','ναι','nai','yes','continue','synexise','synechise','proxora')
        if continuation and self.goal and self.status in ('active','blocked'):
            return self.goal
        if continuation and self.parent_goal and self.status == 'complete':
            self.goal, self.parent_goal = self.parent_goal, ''
            self.kind = classify(self.goal)
            self.status = 'active'
            return self.goal
        kind = classify(goal)
        if selected:
            kind = 'code'
        if kind in ('code','debug','architect','assistant') and (positive_actions(goal) or kind == 'architect'):
            if self.goal and self.status in ('active','blocked') and any(word in normal(text) for word in ('αυτο','this','πρωτα','prota')):
                self.parent_goal = self.goal[:1800]
            else:
                self.parent_goal = ''
            self.goal = goal[:4000]
            self.kind = kind
            self.status = 'active'
        return goal

    def snapshot(self):
        return {'goal':self.goal,'status':self.status,'kind':self.kind,'parent_goal':self.parent_goal,'todos':self.todos}

    def restore(self, data):
        self.clear()
        if isinstance(data,dict):
            self.goal = str(data.get('goal') or '')[:4000]
            self.parent_goal = str(data.get('parent_goal') or '')[:1800]
            self.status = data.get('status') if data.get('status') in ('idle','active','blocked','complete') else 'idle'
            self.kind = data.get('kind') if data.get('kind') in ('ask','code','debug','architect','assistant') else 'ask'
            try: self.set_todos(data.get('todos',[]))
            except ValueError: self.todos=[]

    def set_todos(self, rows):
        if not isinstance(rows,list) or len(rows)>40: raise ValueError('Έως 40 εργασίες.')
        result=[]; ids=set()
        for row in rows:
            if not isinstance(row,dict): raise ValueError('Μη έγκυρη εργασία.')
            key=row.get('id'); text=row.get('text'); status=row.get('status')
            if not isinstance(key,str) or not key or len(key)>80 or key in ids: raise ValueError('Χρειάζονται μοναδικά IDs.')
            if not isinstance(text,str) or not text.strip() or len(text)>500 or status not in ('pending','in_progress','done'): raise ValueError('Μη έγκυρη εργασία.')
            ids.add(key); result.append({'id':key,'text':text.strip(),'status':status})
        self.todos=result

    def prompt(self):
        if not self.goal or self.status not in ('active','blocked'):
            return ''
        text = '\nΤρέχουσα εργασία χρήστη (δεδομένα συνέχειας, η νεότερη οδηγία χρήστη υπερισχύει):\n' + self.goal
        if self.parent_goal:
            text += '\nΑρχική εκκρεμής εργασία:\n' + self.parent_goal
        return text + '\nΚατάσταση: ' + self.status


COMPLETION_SPEC = {'type':'function','function':{
    'name':'complete_task',
    'description':'Finish an authorized implementation after real changes/checks, or report already_satisfied, blocked, needs_input with concrete evidence. For questions simply answer normally. Call this last, after tools and tests.',
    'parameters':{'type':'object','properties':{
        'outcome':{'type':'string','enum':['completed','already_satisfied','blocked','needs_input']},
        'summary':{'type':'string'},
        'evidence':{'type':'string'}},'required':['outcome','summary','evidence'],'additionalProperties':False}}}
