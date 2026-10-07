/** A block's own heading inside a section (the section title is the h2, the block the h3). */
export const SubHeading = ({
  id,
  title,
  desc,
}: {
  id: string;
  title: string;
  desc?: string;
}) => (
  <div className="mb-3">
    <h4 id={id} className="text-sm font-semibold text-ink-heading">
      {title}
    </h4>
    {desc ? <p className="text-xs text-ink-body">{desc}</p> : null}
  </div>
);
