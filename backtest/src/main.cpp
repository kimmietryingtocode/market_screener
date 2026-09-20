#include "backtest.hpp"

#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::string require_value(int& index, int argc, char** argv) {
    if (index + 1 >= argc) {
        throw std::invalid_argument(std::string("missing value for ") + argv[index]);
    }
    return argv[++index];
}

double nonnegative(const std::string& value, const std::string& option) {
    const double parsed = std::stod(value);
    if (parsed < 0.0) throw std::invalid_argument(option + " cannot be negative");
    return parsed;
}

void usage() {
    std::cout
        << "Usage: portfolio_backtest --prices PRICES.csv --targets TARGETS.csv "
           "--output-dir DIR [--benchmark SPY] [--start YYYY-MM-DD] "
           "[--end YYYY-MM-DD] [--cost-bps 10] [--initial-capital 100000]\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        std::string prices_path;
        std::string targets_path;
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
            } else if (option == "--targets") {
                targets_path = require_value(index, argc, argv);
            } else if (option == "--output-dir") {
                output_directory = require_value(index, argc, argv);
            } else if (option == "--benchmark") {
                config.benchmark = require_value(index, argc, argv);
            } else if (option == "--start") {
                config.start_date = backtest::Date::parse(require_value(index, argc, argv));
            } else if (option == "--end") {
                config.end_date = backtest::Date::parse(require_value(index, argc, argv));
            } else if (option == "--cost-bps") {
                config.transaction_cost_bps = nonnegative(require_value(index, argc, argv), option);
            } else if (option == "--initial-capital") {
                config.initial_capital = nonnegative(require_value(index, argc, argv), option);
            } else if (option == "--pit-data" || option == "--universe" ||
                       option == "--scenario-config") {
                throw std::invalid_argument(
                    option + " was retired; generate a dated target schedule with python -m backtest");
            } else {
                throw std::invalid_argument("unknown option: " + option);
            }
        }
        if (prices_path.empty() || targets_path.empty() || output_directory.empty()) {
            usage();
            return 2;
        }
        if (config.initial_capital <= 0.0) {
            throw std::invalid_argument("initial capital must be positive");
        }
        const auto prices = backtest::PriceTable::load_csv(prices_path, config.benchmark);
        const auto targets = backtest::load_target_schedule_csv(targets_path);
        const auto result = backtest::run_target_schedule(prices, targets, config);
        backtest::write_outputs(result, output_directory);
        std::cout << "Target-schedule backtest complete: " << output_directory << "\n"
                  << "Ending value: " << result.portfolio.ending_value << "\n"
                  << "CAGR: " << result.portfolio.cagr * 100.0 << "%\n"
                  << "Maximum drawdown: " << result.portfolio.maximum_drawdown * 100.0 << "%\n"
                  << "Rebalances: " << result.rebalance_count << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}
