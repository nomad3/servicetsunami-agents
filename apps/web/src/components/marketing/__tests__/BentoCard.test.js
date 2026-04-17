import { render, screen } from '@testing-library/react';
import BentoCard from '../BentoCard';
import { FiZap } from 'react-icons/fi';

test('renders title and description', () => {
  render(<BentoCard title="AI Command" description="Run agents from chat." icon={FiZap} />);
  expect(screen.getByText('AI Command')).toBeInTheDocument();
  expect(screen.getByText('Run agents from chat.')).toBeInTheDocument();
});

test('large variant renders with className bento-card--large', () => {
  const { container } = render(<BentoCard title="X" description="Y" large />);
  expect(container.querySelector('.bento-card--large')).toBeInTheDocument();
});
