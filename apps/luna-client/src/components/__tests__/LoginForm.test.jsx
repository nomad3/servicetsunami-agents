import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const loginMock = vi.fn();

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ login: loginMock }),
}));

import LoginForm from '../LoginForm';

beforeEach(() => {
  loginMock.mockReset();
});

describe('LoginForm', () => {
  it('renders email and password inputs', () => {
    render(<LoginForm />);
    expect(screen.getByPlaceholderText('Email')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Password')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('calls login with the entered credentials and disables the button while pending', async () => {
    let resolveLogin;
    loginMock.mockImplementation(
      () => new Promise((resolve) => { resolveLogin = resolve; })
    );

    render(<LoginForm />);
    fireEvent.change(screen.getByPlaceholderText('Email'), {
      target: { value: 'simon@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('Password'), {
      target: { value: 'hunter2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith('simon@example.com', 'hunter2');
    });
    expect(screen.getByRole('button')).toHaveTextContent(/signing in/i);
    expect(screen.getByRole('button')).toBeDisabled();

    resolveLogin();
    await waitFor(() => {
      expect(screen.getByRole('button')).not.toBeDisabled();
    });
  });

  it('renders the error message when login throws', async () => {
    loginMock.mockRejectedValue(new Error('Invalid credentials'));

    render(<LoginForm />);
    fireEvent.change(screen.getByPlaceholderText('Email'), {
      target: { value: 'a@b.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('Password'), {
      target: { value: 'pw' },
    });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText('Invalid credentials')).toBeInTheDocument();
    });
  });
});
