var margin = {top: 30, right: 20, bottom: 40, left: 50},
    width = 1280 - margin.left - margin.right,
    height = 825 - margin.top - margin.bottom;


var parseDate = d3.time.format("%Y-%m-%d").parse;

var x = d3.time.scale().range([0, width]);
var y = d3.scale.linear().range([height, 0]);

var xAxis = d3.svg.axis().scale(x)
  .orient("bottom").ticks(10);

var yAxis = d3.svg.axis().scale(y)
  .orient("left").ticks(25);



var priceline = d3.svg.line()
    .x(function(d) { return x(d.day); })
    .y(function(d) { return y(d.seats); });

var graphparties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green"]


d3.tsv("/election2015/data/trends.csv", function(error, data) {


    data.forEach(function(d) {
        d.day = parseDate(d.day);
        d.seats = +d.seats;

    });

    // Scale the range of the data
    x.domain(d3.extent(data, function(d) { return d.day; }));
    y.domain([0, d3.max(data, function(d) { return d.seats; }) + 25]);


    var dataNest = d3.nest()
        .key(function(d) {return d.party;})
        .entries(data);


    dataNest.forEach(function(d) {
      if (graphparties.indexOf(d.key) >= 0){
        svg.append("path")

          .attr("class", d.key)
          .style("fill", "none")
          .attr("d", priceline(d.values))
        }
    });


    svg.append("g")
        .attr("class", "x axis")
        .attr("transform", "translate(0," + height + ")")
        .attr("transform", "translate(0," + height + ")")
        .call(xAxis);

    svg.append("g")
        .attr("class", "y axis")
        .call(yAxis);



	  svg.append("text")
  	    .attr("class", "x label")
  	    .attr("text-anchor", "end")
  	    .attr("x", width / 2)
  	    .attr("y", height + 35)
  	    .text("Date");


  	svg.append("text")
      .attr("class", "y label")
      .attr("text-anchor", "end")
      .attr("y", - 50)
      .attr("x", -height / 2 + 25)
      .attr("dy", ".75em")
      .attr("transform", "rotate(-90)")
      .text("Seats");

});
