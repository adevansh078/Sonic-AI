// Development launcher only; all backend application logic is Python.
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const backend=fileURLToPath(new URL('../backend/',import.meta.url));
const python=fileURLToPath(new URL(process.platform==='win32'?'../backend/.venv/Scripts/python.exe':'../backend/.venv/bin/python',import.meta.url));
if(!existsSync(python)){console.error('Create backend/.venv and install backend/requirements.lock.txt first. See README.');process.exit(1);}
const args=process.argv[2]==='test'?['-m','unittest','discover','-s','tests','-v']:['run.py'];
const child=spawn(python,args,{cwd:backend,stdio:'inherit'});
child.on('error',error=>{console.error(error.message);process.exit(1);});
child.on('exit',code=>process.exit(code??0));
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
