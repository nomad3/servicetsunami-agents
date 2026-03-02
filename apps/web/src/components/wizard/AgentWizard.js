import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Container, Button, Card } from 'react-bootstrap';
import WizardStepper from './WizardStepper';
import TemplateSelector from './TemplateSelector';
import BasicInfoStep from './BasicInfoStep';
import PersonalityStep from './PersonalityStep';
import SkillsDataStep from './SkillsDataStep';
import ReviewStep from './ReviewStep';
import agentService from '../../services/agent';
import { useToast } from '../common/Toast';
import './AgentWizard.css';

const STEPS = [
  { number: 1, label: 'Template', component: 'TemplateSelector' },
  { number: 2, label: 'Basic Info', component: 'BasicInfo' },
  { number: 3, label: 'Personality', component: 'Personality' },
  { number: 4, label: 'Skills', component: 'SkillsData' },
  { number: 5, label: 'Review', component: 'Review' },
];

const DRAFT_KEY = 'agent_wizard_draft';

const AgentWizard = () => {
  const navigate = useNavigate();
  const { success, error, warning } = useToast();
  const [currentStep, setCurrentStep] = useState(1);
  const [wizardData, setWizardData] = useState({
    template: null,
    basicInfo: { name: '', description: '', avatar: '' },
    personality: { preset: 'friendly', temperature: 0.7, max_tokens: 2000, system_prompt: '' },
    skills: { sql_query: false, data_summary: false, calculator: false, entity_extraction: false, knowledge_search: false, lead_scoring: false },
    scoring_rubric: null,
  });
  const [creating, setCreating] = useState(false);
  const [validationState, setValidationState] = useState({
    step1: false, // Template selected
    step2: false, // Basic info valid
    step3: true,  // Always valid (has defaults)
    step4: true,  // Always valid (optional)
    step5: true,  // Review step
  });

  // Load draft from localStorage on mount
  useEffect(() => {
    const loadDraft = () => {
      try {
        const draft = localStorage.getItem(DRAFT_KEY);
        if (draft) {
          const parsed = JSON.parse(draft);
          if (window.confirm('Resume your previous agent draft?')) {
            setWizardData(parsed.data);
            setCurrentStep(parsed.step);
          } else {
            localStorage.removeItem(DRAFT_KEY);
          }
        }
      } catch (error) {
        console.error('Error loading draft:', error);
        localStorage.removeItem(DRAFT_KEY);
      }
    };

    loadDraft();
  }, []);

  // Auto-save draft to localStorage
  useEffect(() => {
    // Don't save on first render or if no template selected
    if (!wizardData.template) return;

    const saveDraft = () => {
      try {
        localStorage.setItem(
          DRAFT_KEY,
          JSON.stringify({
            data: wizardData,
            step: currentStep,
            timestamp: new Date().toISOString(),
          })
        );
      } catch (error) {
        console.error('Error saving draft:', error);
      }
    };

    // Debounce saves
    const timeoutId = setTimeout(saveDraft, 1000);
    return () => clearTimeout(timeoutId);
  }, [wizardData, currentStep]);

  const handleNext = () => {
    // Validate current step
    if (currentStep === 1 && !wizardData.template) {
      warning('Please select a template to continue');
      return;
    }

    if (currentStep === 2) {
      if (!wizardData.basicInfo.name || wizardData.basicInfo.name.length < 3) {
        warning('Please enter a valid agent name (at least 3 characters)');
        return;
      }
    }

    if (currentStep < STEPS.length) {
      setCurrentStep(currentStep + 1);
    }
  };

  const handleBack = () => {
    if (currentStep > 1) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleCancel = () => {
    if (window.confirm('Cancel wizard? Your progress is auto-saved and you can resume later.')) {
      // Keep draft for resume
      navigate('/agents');
    }
  };

  const updateWizardData = (stepData) => {
    setWizardData({ ...wizardData, ...stepData });
  };

  const handleCreate = async () => {
    try {
      setCreating(true);

      // Build agent config
      const agentData = {
        name: wizardData.basicInfo.name,
        description: wizardData.basicInfo.description,
        config: {
          model: wizardData.template?.config?.model || 'gpt-4',
          temperature: wizardData.personality.temperature,
          max_tokens: wizardData.personality.max_tokens,
          system_prompt: wizardData.personality.system_prompt || wizardData.template?.config?.system_prompt,
          personality_preset: wizardData.personality.preset,
          template_used: wizardData.template?.id,
          avatar: wizardData.basicInfo.avatar,
          tools: Object.entries(wizardData.skills)
            .filter(([_, enabled]) => enabled)
            .map(([tool, _]) => tool),
          entity_schema: wizardData.template?.config?.entity_schema || null,
          scoring_rubric: wizardData.scoring_rubric || null,
        },
      };

      await agentService.create(agentData);

      localStorage.removeItem(DRAFT_KEY);

      // Show success toast before navigation
      success('Agent created successfully!');

      // Navigate after brief delay so toast is visible
      setTimeout(() => {
        navigate('/agents');
      }, 500);
    } catch (err) {
      console.error('Error creating agent:', err);

      // Better error messages based on status
      if (err.response?.status === 401) {
        error('Your session has expired. Please log in again.');
        setTimeout(() => navigate('/login'), 1500);
      } else if (err.response?.status === 400) {
        error(`Invalid data: ${err.response.data.detail || 'Please check your inputs'}`);
      } else {
        error('Failed to create agent. Please try again.');
      }
    } finally {
      setCreating(false);
    }
  };

  return (
    <Container className="wizard-container py-4">
      <Card className="wizard-card">
        <Card.Body>
          <WizardStepper currentStep={currentStep} steps={STEPS} />

          <div className="wizard-content mt-4">
            {currentStep === 1 && (
              <div className="text-center mb-3">
                <small className="text-muted">
                  Experienced user?{' '}
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      if (window.confirm('Switch to quick form? Your wizard progress will be lost.')) {
                        navigate('/agents', { state: { showQuickForm: true } });
                      }
                    }}
                  >
                    Use quick form instead →
                  </a>
                </small>
              </div>
            )}
            {currentStep === 1 && (
              <TemplateSelector
                onSelect={(template) => {
                  updateWizardData({
                    template: template,
                    basicInfo: {
                      ...wizardData.basicInfo,
                      name: template.name
                    },
                    personality: {
                      preset: template.config.personality,
                      temperature: template.config.temperature,
                      max_tokens: template.config.max_tokens,
                      system_prompt: template.config.system_prompt,
                    },
                    skills: template.config.tools.reduce((acc, tool) => {
                      acc[tool] = true;
                      return acc;
                    }, { sql_query: false, data_summary: false, calculator: false, entity_extraction: false, knowledge_search: false, lead_scoring: false }),
                    scoring_rubric: template.config.scoring_rubric || null,
                  });
                  setValidationState({ ...validationState, step1: true });
                }}
                selectedTemplate={wizardData.template?.id}
              />
            )}
            {currentStep === 2 && (
              <BasicInfoStep
                data={wizardData.basicInfo}
                onChange={(basicInfo) => updateWizardData({ basicInfo })}
                onValidationChange={(isValid) => {
                  setValidationState({ ...validationState, step2: isValid });
                }}
              />
            )}
            {currentStep === 3 && (
              <PersonalityStep
                data={wizardData.personality}
                onChange={(personality) => updateWizardData({ personality })}
              />
            )}
            {currentStep === 4 && (
              <SkillsDataStep
                data={{ skills: wizardData.skills }}
                onChange={(skillsData) => updateWizardData(skillsData)}
                templateName={wizardData.template?.name}
              />
            )}
            {currentStep === 5 && (
              <ReviewStep
                wizardData={wizardData}
                onEdit={(step) => setCurrentStep(step)}
              />
            )}
          </div>

          <div className="wizard-actions mt-4 d-flex justify-content-between">
            <div>
              {currentStep > 1 && (
                <Button variant="outline-secondary" onClick={handleBack}>
                  Back
                </Button>
              )}
            </div>
            <div className="d-flex gap-2">
              <Button variant="outline-secondary" onClick={handleCancel}>
                Cancel
              </Button>
              {currentStep < STEPS.length && (
                <Button
                  variant="primary"
                  onClick={handleNext}
                  disabled={!validationState[`step${currentStep}`]}
                >
                  Next
                </Button>
              )}
              {currentStep === STEPS.length && (
                <Button variant="success" onClick={handleCreate} disabled={creating}>
                  {creating ? 'Creating...' : 'Create Agent'}
                </Button>
              )}
            </div>
          </div>
        </Card.Body>
      </Card>
    </Container>
  );
};

export default AgentWizard;
