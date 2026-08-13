#include "base.hpp"

namespace workflow {

class SubCommand : public BaseCommand {
public:
    bool run();
};

bool SubCommand::run() {
    return npuFusedOps();
}

}
