import React, { useState } from 'react';
import { Card, Row, Col, Form, Accordion } from 'react-bootstrap';

const PRESETS = [
  {
    id: 'formal',
    name: 'Formal & Professional',
    emoji: '🎩',
    description: 'Precise, structured responses. Best for business contexts',
    temperature: 0.4,
    max_tokens: 1500,
  },
  {
    id: 'friendly',
    name: 'Friendly & Conversational',
    emoji: '💬',
    description: 'Warm, approachable tone. Great for customer interactions',
    temperature: 0.7,
    max_tokens: 2000,
  },
  {
    id: 'creative',
    name: 'Creative & Expressive',
    emoji: '✨',
    description: 'Imaginative, colorful language. Perfect for content creation',
    temperature: 0.9,
    max_tokens: 3000,
  },
];

const PersonalityStep = ({ data, onChange }) => {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handlePresetSelect = (preset) => {
    onChange({
      ...data,
      preset: preset.id,
      temperature: preset.temperature,
      max_tokens: preset.max_tokens,
    });
  };

  const handleSliderChange = (field, value) => {
    onChange({ ...data, [field]: parseFloat(value) });
  };

  const handlePromptChange = (value) => {
    onChange({ ...data, system_prompt: value });
  };

  return (
    <div className="personality-step">
      <h3 className="mb-2">How should your agent communicate?</h3>
      <p className="text-muted mb-4">Choose a communication style for your agent</p>

      <Row className="g-3 mb-4">
        {PRESETS.map((preset) => (
          <Col key={preset.id} md={4}>
            <Card
              className={`preset-card h-100 ${data.preset === preset.id ? 'selected' : ''}`}
              onClick={() => handlePresetSelect(preset)}
              style={{ cursor: 'pointer' }}
            >
              <Card.Body className="text-center">
                <div className="preset-emoji mb-2" style={{ fontSize: '2.5rem' }}>
                  {preset.emoji}
                </div>
                <Card.Title className="h6">{preset.name}</Card.Title>
                <Card.Text className="text-muted small">
                  {preset.description}
                </Card.Text>
              </Card.Body>
            </Card>
          </Col>
        ))}
      </Row>

      <Accordion className="mb-3">
        <Accordion.Item eventKey="0">
          <Accordion.Header onClick={() => setShowAdvanced(!showAdvanced)}>
            Advanced: Fine-tune settings
          </Accordion.Header>
          <Accordion.Body>
            <Form.Group className="mb-3">
              <Form.Label>
                Response Style: {data.temperature.toFixed(1)}
              </Form.Label>
              <div className="d-flex align-items-center gap-3">
                <small className="text-muted">🎯 Precise</small>
                <Form.Range
                  min={0}
                  max={1}
                  step={0.1}
                  value={data.temperature}
                  onChange={(e) => handleSliderChange('temperature', e.target.value)}
                />
                <small className="text-muted">🎨 Creative</small>
              </div>
              <Form.Text className="text-muted">
                Controls response randomness. Lower = more focused, Higher = more creative
              </Form.Text>
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>
                Response Length: {data.max_tokens} tokens
              </Form.Label>
              <div className="d-flex align-items-center gap-3">
                <small className="text-muted">Concise</small>
                <Form.Range
                  min={500}
                  max={4000}
                  step={100}
                  value={data.max_tokens}
                  onChange={(e) => handleSliderChange('max_tokens', e.target.value)}
                />
                <small className="text-muted">Detailed</small>
              </div>
              <Form.Text className="text-muted">
                Maximum length of agent responses
              </Form.Text>
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label className="d-flex justify-content-between">
                <span>System Prompt</span>
                <small className="text-muted">{(data.system_prompt || '').length} / 4000</small>
              </Form.Label>
              <Form.Control
                as="textarea"
                rows={8}
                placeholder="You are a helpful assistant that..."
                value={data.system_prompt}
                onChange={(e) => handlePromptChange(e.target.value)}
                maxLength={4000}
                style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}
              />
              <Form.Text className="text-muted">
                The system prompt defines your agent's behaviour, tools it uses, and how it responds. The template provides a production-ready default — customize it as needed.
              </Form.Text>
            </Form.Group>
          </Accordion.Body>
        </Accordion.Item>
      </Accordion>
    </div>
  );
};

export default PersonalityStep;
