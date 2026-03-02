import React, { useState } from 'react';
import { Card, Form, Alert } from 'react-bootstrap';
import { FaDatabase as Database, FaCalculator as CalcIcon, FaChartBar as BarChart, FaProjectDiagram, FaSearch, FaChartLine } from 'react-icons/fa';

const TOOLS = [
  {
    id: 'sql_query',
    name: 'Data Analysis',
    icon: Database,
    description: 'Let your agent answer questions about your data',
    requiresDataset: true,
    helpText: 'Enable this if you want your agent to query and analyze datasets',
  },
  {
    id: 'data_summary',
    name: 'Quick Statistics',
    icon: BarChart,
    description: 'Generate summaries and statistics automatically',
    requiresDataset: false,
    helpText: 'Your agent can provide statistical overviews of data',
  },
  {
    id: 'calculator',
    name: 'Math & Calculations',
    icon: CalcIcon,
    description: 'Perform calculations and number crunching',
    requiresDataset: false,
    helpText: 'Enable this for pricing, conversions, or any math needs',
  },
  {
    id: 'entity_extraction',
    name: 'Entity Extraction',
    icon: FaProjectDiagram,
    description: 'Extract people, companies, and concepts from text',
    requiresDataset: false,
    helpText: 'Your agent can identify and store entities from conversations and documents into the knowledge graph',
  },
  {
    id: 'knowledge_search',
    name: 'Knowledge Search',
    icon: FaSearch,
    description: 'Search and browse the knowledge graph',
    requiresDataset: false,
    helpText: 'Your agent can look up people, companies, and concepts previously extracted into the knowledge graph',
  },
  {
    id: 'lead_scoring',
    name: 'Lead Scoring',
    icon: FaChartLine,
    description: 'Score entities 0-100 using configurable rubrics (AI leads, M&A deals, marketing signals)',
    requiresDataset: false,
    helpText: 'Your agent can compute composite lead scores using AI analysis of entity data',
  },
];

const ToolCard = ({ tool, isChecked, onToggle }) => {
  const [showHelp, setShowHelp] = useState(false);
  const IconComponent = tool.icon;

  return (
    <Card key={tool.id} className="mb-2">
      <Card.Body className="py-3">
        <div className="d-flex align-items-start justify-content-between">
          <div className="d-flex align-items-start gap-3 flex-grow-1">
            <div className="tool-icon" style={{ fontSize: '1.5rem', color: '#0d6efd' }}>
              <IconComponent />
            </div>
            <div className="flex-grow-1">
              <div className="d-flex align-items-center gap-2 mb-1">
                <strong>{tool.name}</strong>
                <button
                  className="btn btn-link btn-sm p-0"
                  onClick={() => setShowHelp(!showHelp)}
                  style={{ textDecoration: 'none', fontSize: '0.85rem' }}
                >
                  {showHelp ? 'Hide' : 'Learn more'}
                </button>
              </div>
              <small className="text-muted">{tool.description}</small>
              {showHelp && (
                <div className="alert alert-info mt-2 mb-0 p-2">
                  <small>{tool.helpText}</small>
                </div>
              )}
            </div>
          </div>
          <Form.Check
            type="switch"
            id={`tool-${tool.id}`}
            label=""
            checked={isChecked}
            onChange={onToggle}
            aria-label={tool.name}
          />
        </div>
      </Card.Body>
    </Card>
  );
};

const SkillsDataStep = ({ data, onChange, templateName }) => {
  const handleToolToggle = (toolId) => {
    const updatedSkills = { ...data.skills, [toolId]: !data.skills[toolId] };
    onChange({ ...data, skills: updatedSkills });
  };

  return (
    <div className="skills-data-step">
      <h3 className="mb-2">What can your agent do?</h3>
      <p className="text-muted mb-4">Configure your agent's capabilities</p>

      <Card>
        <Card.Body>
          <h5 className="mb-3">Skills</h5>
          {templateName && (
            <Alert variant="success" className="mb-3">
              <small>
                ✓ Based on your <strong>{templateName}</strong> template, we've pre-selected the recommended tools below. You can enable or disable any of them.
              </small>
            </Alert>
          )}

          {TOOLS.map((tool) => (
            <ToolCard
              key={tool.id}
              tool={tool}
              isChecked={data.skills[tool.id]}
              onToggle={() => handleToolToggle(tool.id)}
            />
          ))}
        </Card.Body>
      </Card>
    </div>
  );
};

export default SkillsDataStep;
