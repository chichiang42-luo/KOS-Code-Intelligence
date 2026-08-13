use crate::ops::npu_fused_ops;

pub trait Command {
    fn run(&self) -> bool;
}

pub struct SubCommand;

impl Command for SubCommand {
    fn run(&self) -> bool {
        npu_fused_ops()
    }
}
