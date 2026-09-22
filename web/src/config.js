/* Copy that is about the demo itself rather than about any one run. */

export const APP = {
  title: "scRNAPipeline",
  tagline: "Claude sequences · Jev decides · Modal runs",
  repo: "https://github.com/20eddibae/scRNAPipeline",
  actors: {
    jev: {
      label: "Jev",
      blurb:
        "TypeSafe's System One model. Answers a typed question with a calibrated " +
        "probability instead of prose. Below the confidence floor the pipeline " +
        "takes the step's declared default and records that it did.",
    },
    claude: {
      label: "Claude",
      blurb:
        "Sequences the run, frames each decision for this dataset, and reads " +
        "ranked marker genes into a cell-type label. Never sets a numeric knob.",
    },
    modal: { label: "Modal", blurb: "Runs the whole thing." },
  },
};
