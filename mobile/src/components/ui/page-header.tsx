/**
 * PageHeading — the logo mark left of the lowercase page title. A drop-in for the
 * old bare `<ScreenTitle>`, used on every screen so headers are consistent (it's
 * what fixed Portfolio's odd one-off header). Screens keep their own right-side
 * actions alongside it.
 */
import { Row } from './layout';
import { Logo } from './logo';
import { ScreenTitle } from './text';
import { spacing } from '@/theme';

export function PageHeading({ title }: { title: string }) {
  return (
    <Row gap={spacing.sm} align="center">
      <Logo size={22} />
      <ScreenTitle>{title}</ScreenTitle>
    </Row>
  );
}
