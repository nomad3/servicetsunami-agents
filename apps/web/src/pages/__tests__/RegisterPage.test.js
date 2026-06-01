import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import RegisterPage from '../RegisterPage';
import authService from '../../services/auth';

jest.mock('../../services/auth', () => ({
  __esModule: true,
  default: { register: jest.fn() },
}));

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('../../__mocks__/react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k) => k }),
}));

const VALID_PASSWORD = 'ValidPass123!';

const fillForm = (password = VALID_PASSWORD) => {
  fireEvent.change(screen.getByPlaceholderText('register.emailPlaceholder'), { target: { value: 'a@b.com' } });
  fireEvent.change(screen.getByPlaceholderText('register.passwordPlaceholder'), { target: { value: password } });
  fireEvent.change(screen.getByPlaceholderText('register.fullNamePlaceholder'), { target: { value: 'Eve' } });
  fireEvent.change(screen.getByPlaceholderText('register.tenantNamePlaceholder'), { target: { value: 'Acme' } });
};

describe('RegisterPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('submits the registration payload', async () => {
    authService.register.mockResolvedValue({ id: 'u1' });
    render(<RegisterPage />);
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'register.submit' }));

    await waitFor(() =>
      expect(authService.register).toHaveBeenCalledWith('a@b.com', VALID_PASSWORD, 'Eve', 'Acme')
    );
    expect(await screen.findByText('register.success')).toBeInTheDocument();
  });

  test('blocks submit and shows a hint when the password is too weak', async () => {
    render(<RegisterPage />);
    fillForm('short1');
    fireEvent.click(screen.getByRole('button', { name: 'register.submit' }));

    expect(await screen.findByText('register.passwordWeak')).toBeInTheDocument();
    expect(authService.register).not.toHaveBeenCalled();
  });

  test('renders a string error detail (e.g. duplicate email)', async () => {
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    authService.register.mockRejectedValue({ response: { data: { detail: 'email taken' } } });
    render(<RegisterPage />);
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'register.submit' }));
    expect(await screen.findByText('email taken')).toBeInTheDocument();
    spy.mockRestore();
  });

  test('renders a 422 validation detail array as a readable message', async () => {
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    authService.register.mockRejectedValue({
      response: {
        data: {
          detail: [
            { loc: ['body', 'user_in', 'password'], msg: 'String should have at least 12 characters' },
          ],
        },
      },
    });
    render(<RegisterPage />);
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'register.submit' }));
    expect(await screen.findByText('String should have at least 12 characters')).toBeInTheDocument();
    spy.mockRestore();
  });
});
