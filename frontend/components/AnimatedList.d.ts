import type { ReactNode } from "react";

export interface AnimatedListProps {
  items?: ReactNode[];
  onItemSelect?: (item: ReactNode, index: number) => void;
  showGradients?: boolean;
  enableArrowNavigation?: boolean;
  className?: string;
  itemClassName?: string;
  displayScrollbar?: boolean;
  initialSelectedIndex?: number;
}

declare const AnimatedList: React.FC<AnimatedListProps>;
export default AnimatedList;
