/*
 * Flot plugin for rendering dashed lines.
 *
 * Allows you to render lines as dashed by setting dashes.show to true
 * in a series options object.
 */

(function ($) {
    function init(plot) {
        plot.hooks.processDatapoints.push(function (plot, series, datapoints) {
            if (!series.dashes || !series.dashes.show) {
                return;
            }
            // Hide regular lines when using dashes
            series.lines.show = false;
        });

        plot.hooks.drawSeries.push(function (plot, ctx, series) {
            if (!series.dashes || !series.dashes.show) {
                return;
            }

            var plotOffset = plot.getPlotOffset();
            var axisx = series.xaxis;
            var axisy = series.yaxis;

            function plotDashes(datapoints, xoffset, yoffset, axisx, axisy) {
                var points = datapoints.points,
                    ps = datapoints.pointsize,
                    prevx = null,
                    prevy = null,
                    dashLength = series.dashes.dashLength || [5, 5];

                ctx.beginPath();
                ctx.setLineDash(dashLength);

                for (var i = ps; i < points.length; i += ps) {
                    var x1 = points[i - ps],
                        y1 = points[i - ps + 1],
                        x2 = points[i],
                        y2 = points[i + 1];

                    if (x1 == null || x2 == null) {
                        continue;
                    }

                    // Clip
                    if (x1 <= x2 && x1 < axisx.min) {
                        if (x2 < axisx.min) continue;
                        y1 = (axisx.min - x1) / (x2 - x1) * (y2 - y1) + y1;
                        x1 = axisx.min;
                    } else if (x2 <= x1 && x2 < axisx.min) {
                        if (x1 < axisx.min) continue;
                        y2 = (axisx.min - x1) / (x2 - x1) * (y2 - y1) + y1;
                        x2 = axisx.min;
                    }

                    if (x1 >= x2 && x1 > axisx.max) {
                        if (x2 > axisx.max) continue;
                        y1 = (axisx.max - x1) / (x2 - x1) * (y2 - y1) + y1;
                        x1 = axisx.max;
                    } else if (x2 >= x1 && x2 > axisx.max) {
                        if (x1 > axisx.max) continue;
                        y2 = (axisx.max - x1) / (x2 - x1) * (y2 - y1) + y1;
                        x2 = axisx.max;
                    }

                    ctx.moveTo(axisx.p2c(x1) + plotOffset.left, axisy.p2c(y1) + plotOffset.top);
                    ctx.lineTo(axisx.p2c(x2) + plotOffset.left, axisy.p2c(y2) + plotOffset.top);
                }

                ctx.stroke();
                ctx.setLineDash([]);
            }

            ctx.save();
            ctx.translate(plotOffset.left, plotOffset.top);
            ctx.lineJoin = "round";

            var lw = series.dashes.lineWidth || series.lines.lineWidth || 2;
            ctx.lineWidth = lw;
            ctx.strokeStyle = series.color;

            ctx.translate(-plotOffset.left, -plotOffset.top);
            plotDashes(series.datapoints, 0, 0, axisx, axisy);
            ctx.restore();
        });
    }

    $.plot.plugins.push({
        init: init,
        options: {
            series: {
                dashes: {
                    show: false,
                    lineWidth: 2,
                    dashLength: [5, 5]
                }
            }
        },
        name: 'dashes',
        version: '1.0'
    });
})(jQuery);
