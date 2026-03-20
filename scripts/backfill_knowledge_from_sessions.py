#!/usr/bin/env python3
"""Extract knowledge from Claude Code sessions and feed into Luna's knowledge graph.

Scans:
1. Claude Code session JSONL files (~25K user messages, ~38K assistant messages)
2. GitHub repos (commit history, README files)
3. docs/plans/ design documents

Extracts:
- Entities: people, projects, companies, technologies, tools
- Relations: works_on, uses, integrates_with, depends_on
- Observations: architecture decisions, patterns, lessons learned
- RL experiences: successful implementations (commits that stuck)

Usage:
    python scripts/backfill_knowledge_from_sessions.py [--project PROJECT_NAME] [--dry-run]
"""

import argparse
import glob
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Add the API app to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'api'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
GITHUB_DIR = os.path.expanduser("~/Documents/GitHub")
DOCS_DIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'plans')

# ─── Session Parsing ──────────────────────────────────────────────

def parse_session_file(filepath: str) -> list[dict]:
    """Parse a JSONL session file into user/assistant message pairs."""
    messages = []
    try:
        with open(filepath) as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    msg_type = d.get('type')
                    if msg_type not in ('user', 'assistant'):
                        continue

                    content = d.get('message', {}).get('content', '')
                    text = ''
                    if isinstance(content, list):
                        text = ' '.join(
                            c.get('text', '') for c in content
                            if isinstance(c, dict) and c.get('type') == 'text'
                        )
                    elif isinstance(content, str):
                        text = content

                    if not text or len(text) < 10:
                        continue

                    messages.append({
                        'type': msg_type,
                        'text': text,
                        'timestamp': d.get('timestamp', ''),
                        'session_id': d.get('sessionId', ''),
                        'git_branch': d.get('gitBranch', ''),
                        'cwd': d.get('cwd', ''),
                        'model': d.get('message', {}).get('model', ''),
                    })
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"Failed to parse {filepath}: {e}")
    return messages


def extract_project_name(project_dir_name: str) -> str:
    """Convert encoded project dir name to human-readable name."""
    # -Users-nomade-Documents-GitHub-servicetsunami-agents -> servicetsunami-agents
    parts = project_dir_name.split('-')
    # Find the last meaningful part (after GitHub)
    try:
        gh_idx = [i for i, p in enumerate(parts) if p == 'GitHub']
        if gh_idx:
            return '-'.join(parts[gh_idx[0] + 1:])
    except:
        pass
    return project_dir_name


# ─── Entity Extraction (Lightweight, no LLM) ─────────────────────

# Known project names from the GitHub directory
KNOWN_PROJECTS = set()
if os.path.isdir(GITHUB_DIR):
    KNOWN_PROJECTS = set(os.listdir(GITHUB_DIR))

# Known technology patterns
TECH_PATTERNS = {
    'languages': ['python', 'javascript', 'typescript', 'go', 'rust', 'java', 'sql', 'html', 'css'],
    'frameworks': ['react', 'fastapi', 'django', 'flask', 'express', 'next.js', 'vue', 'angular',
                   'bootstrap', 'tailwind', 'sqlalchemy', 'pydantic', 'temporal'],
    'tools': ['docker', 'kubernetes', 'k3s', 'terraform', 'helm', 'github actions', 'cloudflare',
              'postgresql', 'pgvector', 'redis', 'anthropic', 'openai', 'gemini', 'claude',
              'tailscale', 'neonize', 'whatsapp', 'jira', 'stripe'],
    'concepts': ['mcp', 'rl', 'reinforcement learning', 'knowledge graph', 'embedding',
                 'oauth', 'jwt', 'multi-tenant', 'webhook', 'temporal workflow'],
}

# People name pattern (TEF-style from bank statements or @mentions)
PERSON_PATTERN = re.compile(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b')


def extract_entities_from_text(text: str, project_name: str = '') -> list[dict]:
    """Extract entities from text using pattern matching (no LLM needed)."""
    entities = []
    text_lower = text.lower()

    # Projects mentioned
    for proj in KNOWN_PROJECTS:
        if proj.lower() in text_lower:
            entities.append({
                'name': proj,
                'entity_type': 'project',
                'category': 'project',
                'properties': {'source': 'claude_code_session'},
            })

    # Technologies
    for category, terms in TECH_PATTERNS.items():
        for term in terms:
            if term in text_lower:
                entities.append({
                    'name': term,
                    'entity_type': 'technology',
                    'category': category,
                    'properties': {'tech_category': category},
                })

    return entities


def extract_observations_from_conversation(messages: list[dict], project_name: str) -> list[dict]:
    """Extract architectural observations from user-assistant conversation pairs."""
    observations = []

    for i, msg in enumerate(messages):
        if msg['type'] != 'user':
            continue

        text = msg['text']

        # Architecture decisions (user describes what they want)
        if any(kw in text.lower() for kw in ['architecture', 'design', 'pattern', 'approach', 'strategy',
                                               'i want to', 'let\'s', 'we should', 'the plan is']):
            observations.append({
                'content': text[:500],
                'observation_type': 'architecture_decision',
                'project': project_name,
                'timestamp': msg.get('timestamp', ''),
            })

        # Debugging patterns (user reports errors)
        if any(kw in text.lower() for kw in ['error', 'bug', 'fix', 'broken', 'not working',
                                               'failed', 'crash', 'issue']):
            # Get the assistant's response if available
            response = ''
            if i + 1 < len(messages) and messages[i + 1]['type'] == 'assistant':
                response = messages[i + 1]['text'][:300]

            observations.append({
                'content': f"Problem: {text[:300]}\nResolution: {response}",
                'observation_type': 'debugging_pattern',
                'project': project_name,
                'timestamp': msg.get('timestamp', ''),
            })

        # Feature implementations
        if any(kw in text.lower() for kw in ['implement', 'build', 'create', 'add feature',
                                               'let\'s add', 'i need']):
            observations.append({
                'content': text[:500],
                'observation_type': 'feature_request',
                'project': project_name,
                'timestamp': msg.get('timestamp', ''),
            })

    return observations


# ─── Git History Extraction ───────────────────────────────────────

def extract_git_history(repo_path: str, max_commits: int = 200) -> list[dict]:
    """Extract commit history from a git repo."""
    commits = []
    try:
        result = subprocess.run(
            ['git', 'log', f'--max-count={max_commits}', '--pretty=format:%H|%an|%ae|%at|%s'],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('|', 4)
            if len(parts) >= 5:
                commits.append({
                    'hash': parts[0],
                    'author': parts[1],
                    'email': parts[2],
                    'timestamp': parts[3],
                    'message': parts[4],
                })
    except Exception as e:
        logger.warning(f"Git history extraction failed for {repo_path}: {e}")
    return commits


# ─── Design Doc Extraction ────────────────────────────────────────

def extract_from_design_docs(docs_dir: str) -> list[dict]:
    """Extract knowledge from markdown design documents."""
    observations = []

    for md_file in glob.glob(os.path.join(docs_dir, '*.md')):
        try:
            with open(md_file) as f:
                content = f.read()

            filename = os.path.basename(md_file)

            # Extract title
            title_match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
            title = title_match.group(1) if title_match else filename

            # Extract date from filename
            date_match = re.match(r'(\d{4}-\d{2}-\d{2})', filename)
            date = date_match.group(1) if date_match else ''

            # Extract sections with headers
            sections = re.split(r'\n##\s+', content)
            for section in sections[1:]:  # Skip the title section
                section_title = section.split('\n')[0].strip()
                section_body = '\n'.join(section.split('\n')[1:]).strip()

                if len(section_body) < 50:
                    continue

                observations.append({
                    'content': f"[{title}] {section_title}: {section_body[:500]}",
                    'observation_type': 'design_document',
                    'project': 'servicetsunami-agents',
                    'timestamp': f"{date}T00:00:00Z" if date else '',
                    'source_file': filename,
                })
        except Exception as e:
            logger.warning(f"Failed to parse {md_file}: {e}")

    return observations


# ─── Database Seeding ─────────────────────────────────────────────

def seed_to_database(entities: list, observations: list, relations: list,
                     tenant_id: str, dry_run: bool = False):
    """Seed extracted data into the knowledge graph."""
    if dry_run:
        logger.info(f"DRY RUN: Would seed {len(entities)} entities, {len(observations)} observations, {len(relations)} relations")
        for e in entities[:10]:
            logger.info(f"  Entity: {e['name']} ({e['entity_type']}/{e.get('category', '')})")
        for o in observations[:10]:
            logger.info(f"  Observation: [{o['observation_type']}] {o['content'][:100]}...")
        return

    from app.db.session import SessionLocal
    from app.models.knowledge_entity import KnowledgeEntity
    from app.models.knowledge_observation import KnowledgeObservation
    from app.models.knowledge_relation import KnowledgeRelation
    from app.services.embedding_service import embed_and_store

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        entities_created = 0
        observations_created = 0
        relations_created = 0

        # Deduplicate entities by name + type
        seen = set()
        unique_entities = []
        for e in entities:
            key = (e['name'].lower(), e['entity_type'])
            if key not in seen:
                seen.add(key)
                unique_entities.append(e)

        # Seed entities
        for e in unique_entities:
            existing = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tid,
                KnowledgeEntity.name == e['name'],
                KnowledgeEntity.entity_type == e['entity_type'],
            ).first()

            if existing:
                continue

            entity = KnowledgeEntity(
                id=uuid.uuid4(),
                tenant_id=tid,
                name=e['name'],
                entity_type=e['entity_type'],
                category=e.get('category', 'general'),
                properties=e.get('properties', {}),
                status='active',
                source='backfill_claude_sessions',
            )
            db.add(entity)
            entities_created += 1

            # Embed entity
            try:
                embed_and_store(
                    db, tenant_id=tid,
                    content_type='knowledge_entity',
                    content_id=str(entity.id),
                    text_content=f"{e['name']} ({e['entity_type']}): {e.get('category', '')}",
                )
            except Exception:
                pass

        db.commit()

        # Seed observations (cap at 500 to avoid overwhelming)
        for o in observations[:500]:
            obs = KnowledgeObservation(
                id=uuid.uuid4(),
                tenant_id=tid,
                content=o['content'][:2000],
                observation_type=o.get('observation_type', 'general'),
                source='backfill_claude_sessions',
                created_at=datetime.utcnow(),
            )
            db.add(obs)
            observations_created += 1

            # Embed observation
            try:
                embed_and_store(
                    db, tenant_id=tid,
                    content_type='knowledge_observation',
                    content_id=str(obs.id),
                    text_content=o['content'][:500],
                )
            except Exception:
                pass

        db.commit()

        # Seed relations between projects and technologies
        for r in relations[:200]:
            existing = db.query(KnowledgeRelation).filter(
                KnowledgeRelation.tenant_id == tid,
                KnowledgeRelation.from_entity_id == r['from_id'],
                KnowledgeRelation.to_entity_id == r['to_id'],
                KnowledgeRelation.relation_type == r['type'],
            ).first()
            if not existing:
                rel = KnowledgeRelation(
                    id=uuid.uuid4(),
                    tenant_id=tid,
                    from_entity_id=r['from_id'],
                    to_entity_id=r['to_id'],
                    relation_type=r['type'],
                )
                db.add(rel)
                relations_created += 1

        db.commit()
        logger.info(f"Seeded: {entities_created} entities, {observations_created} observations, {relations_created} relations")
    finally:
        db.close()


# ─── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Backfill knowledge from Claude Code sessions')
    parser.add_argument('--project', help='Specific project to scan (default: all)')
    parser.add_argument('--tenant-id', default='0f134606-3906-44a5-9e88-6c2020f0f776',
                        help='Tenant UUID to seed data for')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be seeded without writing')
    parser.add_argument('--max-sessions', type=int, default=50, help='Max sessions to process')
    parser.add_argument('--include-git', action='store_true', help='Also scan git commit history')
    parser.add_argument('--include-docs', action='store_true', help='Also scan docs/plans/')
    args = parser.parse_args()

    all_entities = []
    all_observations = []
    all_relations = []

    # 1. Scan Claude Code sessions
    logger.info("=== Scanning Claude Code sessions ===")
    project_dirs = glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, '*'))

    sessions_processed = 0
    for project_dir in sorted(project_dirs):
        if not os.path.isdir(project_dir):
            continue

        project_name = extract_project_name(os.path.basename(project_dir))

        if args.project and args.project not in project_name:
            continue

        session_files = sorted(glob.glob(os.path.join(project_dir, '*.jsonl')))
        if not session_files:
            continue

        logger.info(f"  Project: {project_name} ({len(session_files)} sessions)")

        for sf in session_files[:args.max_sessions]:
            messages = parse_session_file(sf)
            if not messages:
                continue

            sessions_processed += 1

            # Extract entities from user messages
            for msg in messages:
                if msg['type'] == 'user':
                    ents = extract_entities_from_text(msg['text'], project_name)
                    all_entities.extend(ents)

            # Extract observations from conversations
            obs = extract_observations_from_conversation(messages, project_name)
            all_observations.extend(obs)

            logger.info(f"    Session {os.path.basename(sf)}: {len(messages)} messages, "
                       f"{len(obs)} observations")

    logger.info(f"  Sessions processed: {sessions_processed}")

    # 2. Scan design docs
    if args.include_docs or not args.project:
        logger.info("=== Scanning design documents ===")
        docs_dir = os.path.abspath(DOCS_DIR)
        if os.path.isdir(docs_dir):
            doc_obs = extract_from_design_docs(docs_dir)
            all_observations.extend(doc_obs)
            logger.info(f"  Extracted {len(doc_obs)} observations from design docs")

    # 3. Scan git history
    if args.include_git:
        logger.info("=== Scanning git history ===")
        for repo_name in sorted(KNOWN_PROJECTS):
            repo_path = os.path.join(GITHUB_DIR, repo_name)
            if not os.path.isdir(os.path.join(repo_path, '.git')):
                continue

            if args.project and args.project not in repo_name:
                continue

            commits = extract_git_history(repo_path, max_commits=100)
            if commits:
                # Create project entity
                all_entities.append({
                    'name': repo_name,
                    'entity_type': 'project',
                    'category': 'project',
                    'properties': {
                        'total_commits': len(commits),
                        'last_commit': commits[0]['message'] if commits else '',
                        'source': 'git_history',
                    },
                })

                # Extract author entities
                authors = set()
                for c in commits:
                    if c['author'] not in authors:
                        authors.add(c['author'])
                        all_entities.append({
                            'name': c['author'],
                            'entity_type': 'person',
                            'category': 'contributor',
                            'properties': {'email': c['email'], 'source': 'git_history'},
                        })

                logger.info(f"  {repo_name}: {len(commits)} commits, {len(authors)} contributors")

    # 4. Summary
    # Deduplicate
    seen_ents = set()
    unique_entities = []
    for e in all_entities:
        key = (e['name'].lower(), e['entity_type'])
        if key not in seen_ents:
            seen_ents.add(key)
            unique_entities.append(e)

    logger.info(f"\n=== Summary ===")
    logger.info(f"  Unique entities: {len(unique_entities)}")
    logger.info(f"  Observations: {len(all_observations)}")
    logger.info(f"  Entity types: {dict(sorted(((e['entity_type'], sum(1 for x in unique_entities if x['entity_type'] == e['entity_type'])) for e in unique_entities), key=lambda x: -x[1]))}")
    logger.info(f"  Observation types: {dict(sorted(((o['observation_type'], sum(1 for x in all_observations if x['observation_type'] == o['observation_type'])) for o in all_observations), key=lambda x: -x[1]))}")

    # 5. Seed
    seed_to_database(
        unique_entities, all_observations, all_relations,
        tenant_id=args.tenant_id, dry_run=args.dry_run,
    )


if __name__ == '__main__':
    main()
