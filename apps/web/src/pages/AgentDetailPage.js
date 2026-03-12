import { useEffect, useState } from 'react';
import { Badge, Button, Col, Modal, Nav, Row, Spinner } from 'react-bootstrap';
import { useNavigate, useParams } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';
import './AgentDetailPage.css';

const STATUS_COLORS = { active: '#22c55e', error: '#ef4444', inactive: '#94a3b8' };
const ROLE_COLORS = { analyst: '#6f42c1', manager: '#0d6efd', specialist: '#fd7e14' };
const TASK_STATUS_COLORS = {
  completed: '#22c55e', failed: '#ef4444', executing: '#f59e0b',
  thinking: '#f59e0b', queued: '#94a3b8', delegated: '#6f42c1',
};
const PRIORITY_COLORS = { critical: '#ef4444', high: '#f59e0b', normal: '#4dabf7', low: '#94a3b8' };

const AgentDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      agentService.getById(id).then(r => setAgent(r.data)),
      agentService.getTasks().then(r => setTasks((r.data || []).filter(t => t.assigned_agent_id === id))).catch(() => {}),
      agentService.getAll().then(r => setAgents(r.data || [])).catch(() => {}),
    ])
      .catch(err => console.error('Failed to load agent:', err))
      .finally(() => setLoading(false));
  }, [id]);

  const handleDelete = async () => {
    try {
      setDeleting(true);
      await agentService.delete(id);
      navigate('/agents');
    } catch (err) {
      console.error(err);
    } finally {
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <Layout>
        <div className="text-center py-5">
          <Spinner animation="border" size="sm" variant="primary" />
        </div>
      </Layout>
    );
  }

  if (!agent) {
    return (
      <Layout>
        <div className="text-center py-5">
          <p style={{ color: 'var(--color-muted)' }}>Agent not found.</p>
          <Button variant="outline-secondary" size="sm" onClick={() => navigate('/agents')}>Back to Fleet</Button>
        </div>
      </Layout>
    );
  }

  // Merge skills from config and AgentSkill records
  const configSkills = agent.config?.skills || agent.config?.tools || [];
  const agentSkillRecords = agent.skills || [];
  const skillMap = {};
  agentSkillRecords.forEach(s => { skillMap[s.skill_name] = s; });
  configSkills.forEach(s => { if (!skillMap[s]) skillMap[s] = { skill_name: s, proficiency: null, times_used: 0, success_rate: 0 }; });
  const allSkills = Object.values(skillMap);

  // Task stats
  const completedTasks = tasks.filter(t => t.status === 'completed').length;
  const totalTasks = tasks.length;
  const successRate = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  const activeTasks = tasks.filter(t => ['queued', 'thinking', 'executing'].includes(t.status)).length;

  const status = agent.status || 'inactive';

  return (
    <Layout>
      <div className="agent-detail-page" style={{ maxWidth: 1100 }}>
        {/* Header */}
        <div className="detail-header">
          <button
            onClick={() => navigate('/agents')}
            style={{ background: 'none', border: 'none', color: 'var(--color-muted)', cursor: 'pointer', fontSize: '0.82rem', padding: 0, marginBottom: 12 }}
          >
            &larr; Back to Agent Fleet
          </button>

          <div className="d-flex justify-content-between align-items-start">
            <div>
              <div className="d-flex align-items-center gap-2 mb-1">
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: STATUS_COLORS[status] || '#94a3b8' }} />
                <h4 style={{ fontWeight: 600, margin: 0, color: 'var(--color-foreground)' }}>{agent.name}</h4>
              </div>
              <p style={{ fontSize: '0.85rem', color: 'var(--color-muted)', margin: '4px 0 8px 0' }}>
                {agent.description || 'No description'}
              </p>
              <div className="d-flex gap-2 flex-wrap">
                <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: 'var(--surface-contrast, rgba(255,255,255,0.06))', color: 'var(--color-muted)' }}>
                  {agent.config?.model || 'gpt-4'}
                </Badge>
                {agent.role && (
                  <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: ROLE_COLORS[agent.role] || '#6c757d' }}>
                    {agent.role}
                  </Badge>
                )}
                <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: 'rgba(255,255,255,0.1)', color: 'var(--color-muted)' }}>
                  {agent.autonomy_level || 'supervised'}
                </Badge>
              </div>
            </div>
            <div className="d-flex gap-2">
              <Button variant="outline-danger" size="sm" onClick={() => setDeleteConfirm(true)} style={{ fontSize: '0.78rem' }}>
                Delete
              </Button>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <Nav className="tab-nav" as="ul">
          {['overview', 'relations', 'tasks', 'config'].map(tab => (
            <Nav.Item as="li" key={tab}>
              <Nav.Link
                className={activeTab === tab ? 'active' : ''}
                onClick={() => setActiveTab(tab)}
                style={{ textTransform: 'capitalize' }}
              >
                {tab}
              </Nav.Link>
            </Nav.Item>
          ))}
        </Nav>

        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <div>
            {/* Stats */}
            <Row className="g-3 mb-4">
              {[
                { label: 'Total Tasks', value: totalTasks },
                { label: 'Completed', value: completedTasks },
                { label: 'Active', value: activeTasks },
                { label: 'Success Rate', value: `${successRate}%` },
              ].map(s => (
                <Col md={3} sm={6} key={s.label}>
                  <div className="stat-tile">
                    <div className="stat-value">{s.value}</div>
                    <div className="stat-label">{s.label}</div>
                  </div>
                </Col>
              ))}
            </Row>

            {/* Skills */}
            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 12 }}>
              Skills ({allSkills.length})
            </div>
            {allSkills.length === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>No skills configured.</p>
            ) : (
              <Row className="g-2 mb-4">
                {allSkills.map(skill => (
                  <Col md={6} lg={4} key={skill.skill_name}>
                    <div className="skill-card">
                      <div className="d-flex justify-content-between align-items-center mb-1">
                        <span style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                          {skill.skill_name.replace(/_/g, ' ')}
                        </span>
                        {skill.learned_from && (
                          <span style={{ fontSize: '0.65rem', padding: '1px 5px', borderRadius: 3, background: 'rgba(255,255,255,0.06)', color: 'var(--color-muted)' }}>
                            {skill.learned_from}
                          </span>
                        )}
                      </div>
                      {skill.proficiency !== null && skill.proficiency !== undefined && (
                        <div className="d-flex align-items-center gap-2 mb-1">
                          <div className="proficiency-bar" style={{ flex: 1 }}>
                            <div className="fill" style={{ width: `${Math.round(skill.proficiency * 100)}%` }} />
                          </div>
                          <span style={{ fontSize: '0.68rem', color: 'var(--color-muted)', minWidth: 28 }}>
                            {Math.round(skill.proficiency * 100)}%
                          </span>
                        </div>
                      )}
                      <div className="d-flex gap-3" style={{ fontSize: '0.68rem', color: 'var(--color-muted)' }}>
                        <span>Used {skill.times_used || 0}x</span>
                        {skill.success_rate > 0 && <span>Success {Math.round(skill.success_rate * 100)}%</span>}
                      </div>
                    </div>
                  </Col>
                ))}
              </Row>
            )}
          </div>
        )}

        {/* Relations Tab */}
        {activeTab === 'relations' && (
          <div>
            <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '20px 24px' }}>
              {agents.filter(a => a.id !== agent.id).length === 0 ? (
                <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', margin: 0 }}>No other agents in the fleet.</p>
              ) : (
                agents.filter(a => a.id !== agent.id).map(other => (
                  <div key={other.id} className="relation-row">
                    <span style={{ color: 'var(--color-muted)' }}>&harr;</span>
                    <span
                      style={{ color: '#4dabf7', cursor: 'pointer', fontWeight: 500 }}
                      onClick={() => navigate(`/agents/${other.id}`)}
                    >
                      {other.name}
                    </span>
                    <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: 'rgba(255,255,255,0.08)', color: 'var(--color-muted)' }}>
                      {other.role || 'agent'}
                    </Badge>
                    <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginLeft: 'auto' }}>
                      {other.status || 'inactive'}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* Tasks Tab */}
        {activeTab === 'tasks' && (
          <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '20px 24px' }}>
            {tasks.length === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', margin: 0 }}>No tasks assigned to this agent.</p>
            ) : (
              <table className="task-table">
                <thead>
                  <tr>
                    <th>Objective</th>
                    <th>Status</th>
                    <th>Priority</th>
                    <th>Created</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map(task => (
                    <tr key={task.id}>
                      <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {task.objective}
                      </td>
                      <td>
                        <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: TASK_STATUS_COLORS[task.status] || '#94a3b8' }}>
                          {task.status}
                        </Badge>
                      </td>
                      <td>
                        <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: PRIORITY_COLORS[task.priority] || '#94a3b8' }}>
                          {task.priority || 'normal'}
                        </Badge>
                      </td>
                      <td style={{ color: 'var(--color-muted)' }}>
                        {task.created_at ? new Date(task.created_at).toLocaleDateString() : '\u2014'}
                      </td>
                      <td style={{ color: 'var(--color-muted)' }}>
                        {task.confidence != null ? `${Math.round(task.confidence * 100)}%` : '\u2014'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Config Tab */}
        {activeTab === 'config' && (
          <div>
            {/* System Prompt */}
            {(agent.config?.system_prompt || agent.system_prompt) && (
              <div className="mb-4">
                <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
                  System Prompt
                </div>
                <div className="config-block">
                  {agent.config?.system_prompt || agent.system_prompt}
                </div>
              </div>
            )}

            {/* Parameters */}
            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
              Parameters
            </div>
            <Row className="g-3 mb-4">
              {[
                { label: 'Model', value: agent.config?.model || 'gpt-4' },
                { label: 'Temperature', value: agent.config?.temperature ?? 0.7 },
                { label: 'Max Tokens', value: agent.config?.max_tokens ?? 2000 },
                { label: 'Autonomy', value: agent.autonomy_level || 'supervised' },
              ].map(p => (
                <Col md={3} sm={6} key={p.label}>
                  <div className="stat-tile">
                    <div className="stat-label">{p.label}</div>
                    <div style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--color-foreground)', marginTop: 4 }}>
                      {p.value}
                    </div>
                  </div>
                </Col>
              ))}
            </Row>

            {/* Raw Config */}
            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
              Raw Configuration
            </div>
            <div className="config-block">
              {JSON.stringify(agent.config || {}, null, 2)}
            </div>
          </div>
        )}
      </div>

      {/* Delete Modal */}
      <Modal show={deleteConfirm} onHide={() => setDeleteConfirm(false)} centered size="sm">
        <Modal.Body className="text-center py-4">
          <p style={{ fontSize: '0.88rem', fontWeight: 500, marginBottom: 8 }}>
            Delete "{agent.name}"?
          </p>
          <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 20 }}>
            This action cannot be undone.
          </p>
          <div className="d-flex justify-content-center gap-2">
            <Button variant="outline-secondary" size="sm" onClick={() => setDeleteConfirm(false)}>Cancel</Button>
            <Button variant="danger" size="sm" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </div>
        </Modal.Body>
      </Modal>
    </Layout>
  );
};

export default AgentDetailPage;
