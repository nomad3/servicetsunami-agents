import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import ActionApproval from '../ActionApproval';

describe('ActionApproval', () => {
  it('renders nothing when no action is provided', () => {
    const { container } = render(<ActionApproval />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows action type and description', () => {
    render(
      <ActionApproval
        action={{ type: 'send_email', description: 'Send the recap to the team' }}
      />
    );
    expect(screen.getByText('send_email')).toBeInTheDocument();
    expect(screen.getByText('Send the recap to the team')).toBeInTheDocument();
  });

  it('renders the JSON details block when present', () => {
    const action = {
      type: 'create_workflow',
      description: 'Create new workflow',
      details: { steps: 3, retries: 2 },
    };
    render(<ActionApproval action={action} />);
    const pre = screen.getByText(/"steps": 3/);
    expect(pre).toBeInTheDocument();
  });

  it('wires Allow / Deny / Skip buttons to their callbacks', () => {
    const onApprove = vi.fn();
    const onDeny = vi.fn();
    const onDismiss = vi.fn();
    const action = { type: 'noop', description: 'desc' };
    render(
      <ActionApproval
        action={action}
        onApprove={onApprove}
        onDeny={onDeny}
        onDismiss={onDismiss}
      />
    );
    fireEvent.click(screen.getByText('Allow'));
    fireEvent.click(screen.getByText('Deny'));
    fireEvent.click(screen.getByText('Skip'));
    expect(onApprove).toHaveBeenCalledWith(action);
    expect(onDeny).toHaveBeenCalledWith(action);
    expect(onDismiss).toHaveBeenCalled();
  });
});
