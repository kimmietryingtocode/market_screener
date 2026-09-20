#include "backtest.hpp"

#include <cstdlib>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void usage() {
    std::cout
        << "Usage: portfolio_backtest --prices PRICES.csv --universe UNIVERSE.csv "
           "--output-dir DIR [options]\n\n"
        << "Options:\n"
        << "  --benchmark TICKER              Default: SPY\n"
        << "  --start YYYY-MM-DD              Performance start after warm-up\n"
        << "  --end YYYY-MM-DD                Performance end\n"
        << "  --top-sectors N                 Default: 3\n"
        << "  --top-industries N              Default: 2 per sector\n"
        << "  --top-companies N               Default: 2 per industry\n"
        << "  --rebalance-months N             Default: 3\n"
        << "  --trend-days N                   Default: 200; 0 disables\n"
        << "  --cost-bps NUMBER                Default: 10\n"
        << "  --initial-capital NUMBER         Default: 100000\n"
        << "  --max-position NUMBER            Default: 0.15\n"
        << "  --max-sector NUMBER              Default: 0.40\n"
        << "  --weighting equal|inverse-vol    Default: inverse-vol\n";
}

std::string require_value(int& index, int argc, char** argv) {
    if (index + 1 >= argc) {
        throw std::invalid_argument(std::string("missing value for ") + argv[index]);
    }
    return argv[++index];
}

int positive_int(const std::string& value, const std::string& option, bool allow_zero = false) {
    const int parsed = std::stoi(value);
    if (parsed < (allow_zero ? 0 : 1)) {
        throw std::invalid_argument(option + " has an invalid value");
    }
    return parsed;
}

double nonnegative_double(const std::string& value, const std::string& option) {
    const double parsed = std::stod(value);
    if (parsed < 0.0) {
        throw std::invalid_argument(option + " cannot be negative");
    }
    return parsed;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        std::string prices_path;
        std::string universe_path;
        std::string output_directory;
        backtest::Config config;

        for (int index = 1; index < argc; ++index) {
            const std::string option = argv[index];
            if (option == "--help" || option == "-h") {
                usage();
                return 0;
            }
            if (option == "--prices") {
                prices_path = require_value(index, argc, argv);
            } else if (option == "--universe") {
                universe_path = require_value(index, argc, argv);
            } else if (option == "--output-dir") {
                output_directory = require_value(index, argc, argv);
            } else if (option == "--benchmark") {
                config.benchmark = require_value(index, argc, argv);
            } else if (option == "--start") {
                config.start_date = backtest::Date::parse(require_value(index, argc, argv));
            } else if (option == "--end") {
                config.end_date = backtest::Date::parse(require_value(index, argc, argv));
            } else if (option == "--top-sectors") {
                config.top_sectors = positive_int(require_value(index, argc, argv), option);
            } else if (option == "--top-industries") {
                config.top_industries_per_sector =
                    positive_int(require_value(index, argc, argv), option);
            } else if (option == "--top-companies") {
                config.top_companies_per_industry =
                    positive_int(require_value(index, argc, argv), option);
            } else if (option == "--rebalance-months") {
                config.rebalance_months = positive_int(require_value(index, argc, argv), option);
            } else if (option == "--trend-days") {
                config.trend_days =
                    positive_int(require_value(index, argc, argv), option, true);
            } else if (option == "--cost-bps") {
                config.transaction_cost_bps =
                    nonnegative_double(require_value(index, argc, argv), option);
            } else if (option == "--initial-capital") {
                config.initial_capital =
                    nonnegative_double(require_value(index, argc, argv), option);
            } else if (option == "--max-position") {
                config.maximum_position_weight =
                    nonnegative_double(require_value(index, argc, argv), option);
            } else if (option == "--max-sector") {
                config.maximum_sector_weight =
                    nonnegative_double(require_value(index, argc, argv), option);
            } else if (option == "--weighting") {
                const std::string value = require_value(index, argc, argv);
                if (value == "equal") {
                    config.inverse_volatility_weights = false;
                } else if (value == "inverse-vol") {
                    config.inverse_volatility_weights = true;
                } else {
                    throw std::invalid_argument("--weighting must be equal or inverse-vol");
                }
            } else {
                throw std::invalid_argument("unknown option: " + option);
            }
        }

        if (prices_path.empty() || universe_path.empty() || output_directory.empty()) {
            usage();
            return 2;
        }
        if (config.initial_capital <= 0.0 || config.maximum_position_weight > 1.0 ||
            config.maximum_sector_weight > 1.0) {
            throw std::invalid_argument("capital must be positive and weight limits must be 0..1");
        }

        auto prices = backtest::PriceTable::load_csv(prices_path, config.benchmark);
        auto universe = backtest::load_universe_csv(universe_path);
        backtest::Engine engine(std::move(prices), std::move(universe), config);
        const auto result = engine.run();
        backtest::write_outputs(result, output_directory);

        std::cout << "Backtest complete: " << output_directory << "\n"
                  << "Ending value: " << result.portfolio.ending_value << "\n"
                  << "CAGR: " << result.portfolio.cagr * 100.0 << "%\n"
                  << "Maximum drawdown: " << result.portfolio.maximum_drawdown * 100.0
                  << "%\n"
                  << "Rebalances: " << result.rebalance_count << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}
