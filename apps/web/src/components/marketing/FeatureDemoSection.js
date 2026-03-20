import { useState } from 'react';
import { Col, Container, Nav, Row, Tab } from 'react-bootstrap';
import { FaBrain, FaChartBar, FaComments, FaDatabase, FaPlay, FaRobot } from 'react-icons/fa';
import AnimatedSection from '../common/AnimatedSection';

const features = [
  {
    key: 'orchestration',
    title: 'Agent Orchestration',
    icon: FaRobot,
    description: 'Visually design and deploy complex multi-agent workflows in minutes.',
  },
  {
    key: 'memory',
    title: 'Memory Systems',
    icon: FaBrain,
    description: 'Inspect and manage the semantic knowledge graph that powers your agents.',
  },
  {
    key: 'chat',
    title: 'Interactive Chat',
    icon: FaComments,
    description: 'Collaborate with your agents in real-time with rich context awareness.',
  },
];

/* ------------------------------------------------------------------ */
/*  Mockup sub-components                                             */
/* ------------------------------------------------------------------ */

const mockupStyles = {
  container: {
    background: 'var(--surface-page)',
    minHeight: 380,
    padding: 0,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    fontSize: 13,
    color: 'var(--color-foreground)',
    overflow: 'hidden',
    borderRadius: '0 0 12px 12px',
  },
  sidebar: {
    width: 200,
    minWidth: 200,
    background: 'var(--surface-elevated)',
    borderRight: '1px solid var(--color-border)',
    padding: '14px 0',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  sidebarItem: (active) => ({
    padding: '7px 16px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12,
    color: active ? 'var(--color-foreground)' : 'var(--color-muted)',
    background: active ? 'var(--surface-contrast)' : 'transparent',
    borderLeft: active ? '2px solid var(--color-primary)' : '2px solid transparent',
    cursor: 'default',
  }),
  sidebarLabel: {
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: 'var(--color-muted)',
    padding: '10px 16px 4px',
    fontWeight: 600,
  },
  mainArea: {
    flex: 1,
    padding: 20,
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  topBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  badge: (color) => ({
    display: 'inline-block',
    padding: '2px 10px',
    borderRadius: 999,
    fontSize: 10,
    fontWeight: 600,
    background: color === 'green' ? 'rgba(43,125,233,0.12)' : color === 'blue' ? 'rgba(59,130,246,0.15)' : 'rgba(180,200,220,0.15)',
    color: color === 'green' ? '#2b7de9' : color === 'blue' ? '#60a5fa' : 'var(--color-muted)',
    marginLeft: 8,
  }),
  card: {
    background: 'var(--surface-elevated)',
    border: '1px solid var(--color-border)',
    borderRadius: 10,
    padding: 14,
  },
  nodeCard: (accent) => ({
    background: 'var(--surface-elevated)',
    border: `1px solid ${accent || 'var(--color-border)'}`,
    borderRadius: 10,
    padding: '12px 14px',
    minWidth: 150,
    position: 'relative',
  }),
  connector: {
    width: 32,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  connectorLine: {
    width: '100%',
    height: 2,
    background: 'linear-gradient(90deg, var(--color-primary), rgba(43,125,233,0.3))',
  },
  progressBar: (pct, color) => ({
    height: 6,
    borderRadius: 3,
    background: 'var(--surface-contrast)',
    overflow: 'hidden',
    flex: 1,
    position: 'relative',
  }),
  progressFill: (pct, color) => ({
    height: '100%',
    width: `${pct}%`,
    borderRadius: 3,
    background: color || 'var(--color-primary)',
  }),
};

/* --- Orchestration Mockup --- */
const OrchestrationMockup = () => {
  const agents = [
    { name: 'Supervisor', role: 'Orchestrator', status: 'Running', color: '#2b7de9', icon: FaRobot, tasks: 12 },
    { name: 'Data Analyst', role: 'Sub-Agent', status: 'Active', color: '#60a5fa', icon: FaDatabase, tasks: 8 },
    { name: 'Report Gen', role: 'Sub-Agent', status: 'Idle', color: '#a78bfa', icon: FaChartBar, tasks: 5 },
  ];

  return (
    <div style={mockupStyles.container}>
      <div style={{ display: 'flex', height: '100%', minHeight: 380 }}>
        {/* Sidebar */}
        <div style={mockupStyles.sidebar}>
          <div style={mockupStyles.sidebarLabel}>AI Assistant</div>
          <div style={mockupStyles.sidebarItem(false)}><FaComments size={12} /> Chat</div>
          <div style={mockupStyles.sidebarItem(true)}><FaRobot size={12} /> Agents</div>
          <div style={mockupStyles.sidebarItem(false)}><FaBrain size={12} /> Memory</div>
          <div style={mockupStyles.sidebarLabel}>Workspace</div>
          <div style={mockupStyles.sidebarItem(false)}><FaDatabase size={12} /> Data Sources</div>
          <div style={mockupStyles.sidebarItem(false)}><FaChartBar size={12} /> Pipelines</div>
        </div>

        {/* Main */}
        <div style={mockupStyles.mainArea}>
          <div style={mockupStyles.topBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 15 }}>Agent Orchestration</span>
              <span style={mockupStyles.badge('green')}>3 Active</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ ...mockupStyles.badge('blue'), cursor: 'default' }}>+ New Agent</span>
            </div>
          </div>

          {/* Workflow graph area */}
          <div style={{ ...mockupStyles.card, flex: 1, display: 'flex', flexDirection: 'column', gap: 16, padding: 20 }}>
            <div style={{ fontSize: 11, color: 'var(--color-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>
              Workflow: Customer Support Pipeline
            </div>

            {/* Agent nodes row */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0, flexWrap: 'wrap' }}>
              {agents.map((agent, i) => (
                <div key={agent.name} style={{ display: 'flex', alignItems: 'center' }}>
                  <div style={mockupStyles.nodeCard(agent.color + '44')}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <div style={{ width: 28, height: 28, borderRadius: 8, background: agent.color + '22', display: 'flex', alignItems: 'center', justifyContent: 'center', color: agent.color }}>
                        <agent.icon size={13} />
                      </div>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 12, lineHeight: 1.2 }}>{agent.name}</div>
                        <div style={{ fontSize: 10, color: 'var(--color-muted)' }}>{agent.role}</div>
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 10 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: agent.status === 'Running' ? '#2b7de9' : agent.status === 'Active' ? '#60a5fa' : '#94a3b8', display: 'inline-block' }} />
                        {agent.status}
                      </span>
                      <span style={{ color: 'var(--color-muted)' }}>{agent.tasks} tasks</span>
                    </div>
                  </div>
                  {i < agents.length - 1 && (
                    <div style={mockupStyles.connector}>
                      <div style={mockupStyles.connectorLine} />
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Metrics row */}
            <div style={{ display: 'flex', gap: 12, marginTop: 'auto' }}>
              {[
                { label: 'Tasks Completed', value: '247', trend: '+18%' },
                { label: 'Avg. Latency', value: '1.2s', trend: '-5%' },
                { label: 'Success Rate', value: '99.4%', trend: '+0.3%' },
              ].map((m) => (
                <div key={m.label} style={{ flex: 1, background: 'var(--surface-contrast)', borderRadius: 8, padding: '10px 12px' }}>
                  <div style={{ fontSize: 10, color: 'var(--color-muted)', marginBottom: 2 }}>{m.label}</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <span style={{ fontSize: 16, fontWeight: 700 }}>{m.value}</span>
                    <span style={{ fontSize: 10, color: '#2b7de9' }}>{m.trend}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/* --- Memory / Knowledge Graph Mockup --- */
const MemoryMockup = () => {
  const entities = [
    { name: 'Customer Onboarding', type: 'Process', relations: 14, confidence: 94, color: '#2b7de9' },
    { name: 'Billing API', type: 'System', relations: 9, confidence: 87, color: '#60a5fa' },
    { name: 'Support Escalation', type: 'Workflow', relations: 7, confidence: 91, color: '#a78bfa' },
    { name: 'User Preferences', type: 'Entity', relations: 12, confidence: 82, color: '#f59e0b' },
    { name: 'Product Catalog', type: 'Dataset', relations: 18, confidence: 96, color: '#2b7de9' },
  ];

  return (
    <div style={mockupStyles.container}>
      <div style={{ display: 'flex', height: '100%', minHeight: 380 }}>
        {/* Sidebar */}
        <div style={mockupStyles.sidebar}>
          <div style={mockupStyles.sidebarLabel}>AI Assistant</div>
          <div style={mockupStyles.sidebarItem(false)}><FaComments size={12} /> Chat</div>
          <div style={mockupStyles.sidebarItem(false)}><FaRobot size={12} /> Agents</div>
          <div style={mockupStyles.sidebarItem(true)}><FaBrain size={12} /> Memory</div>
          <div style={mockupStyles.sidebarLabel}>Workspace</div>
          <div style={mockupStyles.sidebarItem(false)}><FaDatabase size={12} /> Data Sources</div>
          <div style={mockupStyles.sidebarItem(false)}><FaChartBar size={12} /> Pipelines</div>
        </div>

        {/* Main */}
        <div style={mockupStyles.mainArea}>
          <div style={mockupStyles.topBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 15 }}>Knowledge Graph</span>
              <span style={mockupStyles.badge('green')}>142 Entities</span>
              <span style={mockupStyles.badge('blue')}>89 Relations</span>
            </div>
          </div>

          {/* Entity table */}
          <div style={{ ...mockupStyles.card, flex: 1, padding: 0, overflow: 'hidden' }}>
            {/* Table header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: '1.5fr 0.8fr 0.7fr 1.2fr',
              padding: '10px 16px',
              fontSize: 10,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              color: 'var(--color-muted)',
              borderBottom: '1px solid var(--color-border)',
              background: 'var(--surface-contrast)',
            }}>
              <span>Entity</span>
              <span>Type</span>
              <span>Relations</span>
              <span>Confidence</span>
            </div>

            {/* Rows */}
            {entities.map((ent, i) => (
              <div key={ent.name} style={{
                display: 'grid',
                gridTemplateColumns: '1.5fr 0.8fr 0.7fr 1.2fr',
                padding: '10px 16px',
                fontSize: 12,
                alignItems: 'center',
                borderBottom: i < entities.length - 1 ? '1px solid var(--color-border)' : 'none',
                transition: 'background 0.15s',
              }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 500 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: ent.color, flexShrink: 0 }} />
                  {ent.name}
                </span>
                <span>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: 6,
                    fontSize: 10,
                    background: ent.color + '18',
                    color: ent.color,
                    fontWeight: 500,
                  }}>
                    {ent.type}
                  </span>
                </span>
                <span style={{ color: 'var(--color-soft)' }}>{ent.relations}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={mockupStyles.progressBar(ent.confidence)}>
                    <div style={mockupStyles.progressFill(ent.confidence, ent.color)} />
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--color-soft)', minWidth: 28, textAlign: 'right' }}>{ent.confidence}%</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

/* --- Chat Mockup --- */
const ChatMockup = () => {
  const messages = [
    { role: 'user', text: 'Summarize last week\'s pipeline failures and suggest fixes.' },
    {
      role: 'assistant',
      text: 'I found 3 pipeline failures last week. The main issue was a schema mismatch in the Bronze layer ingestion. Here\'s the breakdown:',
      extra: (
        <div style={{ marginTop: 8, background: 'var(--surface-contrast)', borderRadius: 8, padding: '10px 12px', fontSize: 11 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#f87171', fontWeight: 500 }}>customer_events pipeline</span>
            <span style={{ color: 'var(--color-muted)' }}>Feb 4, 14:32</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#f87171', fontWeight: 500 }}>product_sync pipeline</span>
            <span style={{ color: 'var(--color-muted)' }}>Feb 5, 09:15</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: '#fbbf24', fontWeight: 500 }}>analytics_rollup pipeline</span>
            <span style={{ color: 'var(--color-muted)' }}>Feb 6, 22:01</span>
          </div>
        </div>
      ),
    },
    { role: 'user', text: 'Fix the schema mismatch and re-run the customer_events pipeline.' },
  ];

  const suggestions = [
    'Show pipeline run history',
    'Optimize data ingestion',
    'Create new agent workflow',
  ];

  return (
    <div style={mockupStyles.container}>
      <div style={{ display: 'flex', height: '100%', minHeight: 380 }}>
        {/* Sidebar */}
        <div style={mockupStyles.sidebar}>
          <div style={mockupStyles.sidebarLabel}>AI Assistant</div>
          <div style={mockupStyles.sidebarItem(true)}><FaComments size={12} /> Chat</div>
          <div style={mockupStyles.sidebarItem(false)}><FaRobot size={12} /> Agents</div>
          <div style={mockupStyles.sidebarItem(false)}><FaBrain size={12} /> Memory</div>
          <div style={mockupStyles.sidebarLabel}>Workspace</div>
          <div style={mockupStyles.sidebarItem(false)}><FaDatabase size={12} /> Data Sources</div>
          <div style={mockupStyles.sidebarItem(false)}><FaChartBar size={12} /> Pipelines</div>
        </div>

        {/* Main */}
        <div style={{ ...mockupStyles.mainArea, padding: 0 }}>
          {/* Chat messages area */}
          <div style={{ flex: 1, padding: '16px 20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {messages.map((msg, i) => (
              <div key={i} style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
              }}>
                <div style={{
                  maxWidth: '82%',
                  padding: '10px 14px',
                  borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                  background: msg.role === 'user' ? 'rgba(43,125,233,0.1)' : 'var(--surface-elevated)',
                  border: msg.role === 'user' ? '1px solid rgba(43,125,233,0.2)' : '1px solid var(--color-border)',
                  fontSize: 12,
                  lineHeight: 1.55,
                  color: 'var(--color-foreground)',
                }}>
                  {msg.role === 'assistant' && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, fontSize: 10, color: 'var(--color-primary)', fontWeight: 600 }}>
                      <FaRobot size={10} /> wolfpoint.ai
                    </div>
                  )}
                  {msg.text}
                  {msg.extra}
                </div>
              </div>
            ))}
          </div>

          {/* Suggestions */}
          <div style={{ padding: '0 20px 8px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {suggestions.map((s) => (
              <span key={s} style={{
                padding: '5px 12px',
                borderRadius: 999,
                fontSize: 10,
                border: '1px solid var(--color-border)',
                color: 'var(--color-soft)',
                background: 'var(--surface-elevated)',
                cursor: 'default',
                whiteSpace: 'nowrap',
              }}>
                {s}
              </span>
            ))}
          </div>

          {/* Input area */}
          <div style={{
            padding: '12px 20px',
            borderTop: '1px solid var(--color-border)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}>
            <div style={{
              flex: 1,
              padding: '10px 14px',
              borderRadius: 10,
              border: '1px solid var(--color-border)',
              background: 'var(--surface-contrast)',
              fontSize: 12,
              color: 'var(--color-muted)',
            }}>
              Ask your agents anything...
            </div>
            <div style={{
              width: 34,
              height: 34,
              borderRadius: 10,
              background: 'var(--color-primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              flexShrink: 0,
            }}>
              <FaPlay size={11} style={{ marginLeft: 2 }} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/*  Tab content mapping                                               */
/* ------------------------------------------------------------------ */

const mockupComponents = {
  orchestration: OrchestrationMockup,
  memory: MemoryMockup,
  chat: ChatMockup,
};

/* ------------------------------------------------------------------ */
/*  Main Section                                                      */
/* ------------------------------------------------------------------ */

const FeatureDemoSection = () => {
  const [activeTab, setActiveTab] = useState('orchestration');

  return (
    <section className="section-wrapper section-dark overflow-hidden">
      <Container>
        <AnimatedSection animation="fade-in">
          <div className="text-center mb-5">
            <h2 className="display-4 fw-bold gradient-text">
              Platform Tour
            </h2>
            <p className="section-subtitle">
              See how wolfpoint.ai empowers your workflow
            </p>
          </div>
        </AnimatedSection>

        <Tab.Container activeKey={activeTab} onSelect={(k) => setActiveTab(k)}>
          <Row className="g-5 align-items-center">
            <Col lg={4}>
              <div className="d-flex flex-column gap-3">
                {features.map((feature) => (
                  <Nav.Link
                    key={feature.key}
                    eventKey={feature.key}
                    className={`feature-tab p-4 rounded-4 border transition-all ${activeTab === feature.key
                        ? 'bg-primary bg-opacity-10 border-primary border-opacity-50 shadow-lg'
                        : 'bg-transparent border-transparent text-soft hover-bg-dark'
                      }`}
                    style={{ cursor: 'pointer', transition: 'all 0.3s ease' }}
                  >
                    <div className="d-flex align-items-center gap-3 mb-2">
                      <div className={`p-2 rounded-circle ${activeTab === feature.key ? 'bg-primary text-white' : 'bg-light text-soft'}`}>
                        <feature.icon size={20} />
                      </div>
                      <h5 className={`mb-0 fw-semibold ${activeTab === feature.key ? '' : 'text-soft'}`}>
                        {feature.title}
                      </h5>
                    </div>
                    <p className={`mb-0 small ${activeTab === feature.key ? 'text-light' : 'text-muted'}`}>
                      {feature.description}
                    </p>
                  </Nav.Link>
                ))}
              </div>
            </Col>

            <Col lg={8}>
              <Tab.Content>
                {features.map((feature) => {
                  const MockupComponent = mockupComponents[feature.key];
                  return (
                    <Tab.Pane key={feature.key} eventKey={feature.key} className="position-relative">
                      <AnimatedSection animation="scale-up">
                        <div className="video-frame p-2 rounded-4 bg-white border border-secondary border-opacity-25 shadow-lg position-relative">
                          {/* Browser Chrome Mockup */}
                          <div className="d-flex align-items-center gap-2 px-3 py-2 border-bottom border-secondary border-opacity-25 mb-0 bg-light bg-opacity-75 rounded-top-3">
                            <div className="d-flex gap-1">
                              <div className="rounded-circle bg-danger" style={{ width: '10px', height: '10px' }}></div>
                              <div className="rounded-circle bg-warning" style={{ width: '10px', height: '10px' }}></div>
                              <div className="rounded-circle bg-success" style={{ width: '10px', height: '10px' }}></div>
                            </div>
                            <div className="mx-auto bg-white bg-opacity-75 px-4 py-1 rounded-pill text-muted small font-monospace" style={{ fontSize: '10px' }}>
                              app.wolfpoint.ai/{feature.key}
                            </div>
                          </div>

                          {/* Platform Mockup */}
                          <MockupComponent />
                        </div>
                      </AnimatedSection>
                    </Tab.Pane>
                  );
                })}
              </Tab.Content>
            </Col>
          </Row>
        </Tab.Container>
      </Container>
    </section>
  );
};

export default FeatureDemoSection;
