import { SegmentedControl } from "../ui/segmented-control";
import { useI18n } from "../../hooks/use-i18n";

export interface FontSizeSelectorProps {
  fontSize: string;
  onChange: (size: string) => void;
}

const optionKeys = ["compact", "default", "comfortable"] as const;

const keyMap: Record<
  string,
  "ui.fontSize.compact" | "ui.fontSize.default" | "ui.fontSize.comfortable"
> = {
  compact: "ui.fontSize.compact",
  default: "ui.fontSize.default",
  comfortable: "ui.fontSize.comfortable",
};

export const FontSizeSelector = ({
  fontSize,
  onChange,
}: FontSizeSelectorProps) => {
  const { t } = useI18n();

  return (
    <SegmentedControl
      value={fontSize}
      onValueChange={onChange}
      className="h-8 w-[min(240px,52vw)] rounded-md p-0.5"
      buttonClassName="rounded-[5px]"
      options={optionKeys.map((key) => ({
        value: key,
        label: t(keyMap[key]),
      }))}
    />
  );
};
