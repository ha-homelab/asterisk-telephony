#!/usr/bin/env python3
"""Generate editable, self-contained technical SVG diagrams. No dependencies."""
from html import escape
from pathlib import Path
import argparse
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
BG='#101c34'; TEXT='#f5f7fb'; TEAL='#48ddcc'; CORAL='#ff836d'; GOLD='#f7cb64'; MUTED='#aab9d0'

def t(text,x,y,size=24,color=TEXT,anchor='start'):
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial,sans-serif" font-size="{size}" text-anchor="{anchor}">'+''.join(f'<tspan x="{x}" dy="{0 if i==0 else size*1.35}">{escape(line)}</tspan>' for i,line in enumerate(text.split('\n')))+'</text>'

def box(text,x,y,w=310,h=125,color=TEAL):
    n=text.count('\n')+1
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#172841" stroke="{color}" stroke-width="2"/>'+t(text,x+w/2,y+h/2-((n-1)*30)/2+8,23,anchor='middle')

def arrow(x1,y1,x2,y2,color=TEAL,both=False):
    start = f' marker-start="url(#arrow-{color[1:]})"' if both else ''
    return f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="{color}" stroke-width="3" marker-end="url(#arrow-{color[1:]})"{start}/>'

def wrap(title,description,content):
    markers=''.join(f'<marker id="arrow-{color[1:]}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8" fill="{color}"/></marker>' for color in [TEAL,CORAL,GOLD])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 760" role="img" aria-labelledby="title desc"><title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc><defs>{markers}</defs><rect width="1280" height="760" fill="{BG}"/>'+t(title,50,65,38)+content+'</svg>\n'

def scenes():
    topology=box('Cisco SIP phones\nG.711 ulaw / alaw',50,300)+box('Asterisk / PJSIP\nphone registration\ncall routing + codec',475,300)+box('Home Assistant\ncustom integration\nAMI monitoring / actions',900,130,color=GOLD)+box('Home Assistant VoIP\nAssist pipeline\nOpus media',900,470,color=CORAL)
    topology+=arrow(360,360,475,360,both=True)+t('SIP + RTP',420,330,19,TEAL,'middle')
    topology+=arrow(785,330,900,225,GOLD,both=True)+t('AMI TCP 5038',810,185,19,GOLD,'middle')+t('HA opens connection',805,214,16,GOLD,'middle')
    topology+=arrow(785,400,900,525,CORAL,both=True)+t('SIP UDP + RTP',810,585,19,CORAL,'middle')
    topology+=t('Separate paths: AMI carries control and state. Assist requires working audio transcoding.',50,695,22,MUTED)
    rollout=box('Pinned upstream ZIP\ncommit + SHA-256',50,180,color=GOLD)+box('Local verification\narchive safety checks',485,180)+box('Staged component\nno HA changes',920,180,w=310)
    rollout+=arrow(360,240,485,240)+arrow(795,240,920,240)
    rollout+=box('Explicit install\nbackup + config check',920,430,w=310,color=CORAL)+box('Map real entity IDs\nrender package / dashboard',485,430)+box('Acceptance tests\nphone pair + DTMF',50,430,color=GOLD)
    rollout+=arrow(1075,305,1075,430,CORAL)+arrow(920,490,795,490)+arrow(485,490,360,490)
    rollout+=t('HACS is an alternative installer. Do not let HACS and this staging tool manage the same directory.',50,695,21,MUTED)
    privacy=box('Public repository\ncode, docs, fake examples',50,170,color=TEAL)+box('Private local files\nentity map, rendered YAML',485,170,color=GOLD)+box('HA config / .storage\nAMI secret and state',920,170,w=310,color=CORAL)
    privacy+=arrow(360,232,485,232)+arrow(795,232,920,232)
    privacy+=box('PBX firewall\nallow only HA source /32\nno public AMI',150,435,w=410,color=GOLD)+box('Recorder exclusions\ncall / DTMF entities\nno history by default',710,435,w=410,color=TEAL)
    privacy+=t('An AMI account with write=system is privileged. Network limits and trusted HA users are required.',50,695,21,MUTED)
    controls=box('Home Assistant UI\ncontrols + schedules',50,180,w=330,h=150)+box('Private phone gateway\nsource + bearer checks',475,180,w=330,h=150,color=GOLD)+box('Cisco 7945G handset\nprimary + secondary line',900,180,w=330,h=150,color=CORAL)
    controls+=arrow(380,255,475,255)+t('HTTP API',428,230,17,TEAL,'middle')
    controls+=arrow(805,255,900,255,CORAL)+t('Cisco XML',852,230,17,CORAL,'middle')
    controls+=box('HA schedule storage\nclaim before sending\nnever replay missed alarms',50,445,w=330,h=150)+box('Asterisk AMI state\nexact channel ownership\nPBX DND + bounded calls',475,445,w=330,h=150,color=GOLD)+box('Private image folders\nlocal PNG / JPEG only\nno URL fetch or symlinks',900,445,w=330,h=150,color=CORAL)
    controls+=arrow(215,330,215,445)+arrow(640,330,640,445,GOLD,both=True)
    controls+=t('Existing SIP audio, Asterisk entities and native Assist pipelines remain independent.',50,670,22,MUTED)
    controls+=t('Message inputs require actual Recorder exclusions. Secrets and household state stay private.',50,710,21,MUTED)
    return {'architecture.svg':wrap('Asterisk and Home Assistant', 'Two independent paths for AMI monitoring and SIP audio to Assist.',topology), 'installation.svg':wrap('Installation and verification', 'Verify upstream software, stage locally, install explicitly, map real entities, then test.',rollout), 'privacy.svg':wrap('Data and trust boundaries', 'Public source is separate from household state and AMI secrets.',privacy), 'phone-controls.svg':wrap('Phone controls, timers and alarms', 'The Home Assistant client uses a private gateway for state-guarded phone actions, with persistent at-most-once schedules.',controls)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    target=ROOT/'docs/diagrams'; target.mkdir(parents=True,exist_ok=True)
    for name,data in scenes().items():
        ET.fromstring(data)
        path=target/name
        if args.check:
            if not path.exists() or path.read_text()!=data:
                raise SystemExit(f'Diagram out of date: {name}')
        else: path.write_text(data)
    print('SVG diagrams verified' if args.check else 'SVG diagrams generated')

if __name__=='__main__':main()
