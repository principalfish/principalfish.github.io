var margin = {top: 40, right: 20, bottom: 40, left: 50},
    width = 1000 - margin.left - margin.right,
    height = 875 - margin.top - margin.bottom;

var currentState = "seats"

var graphParties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green"]

function drawGraph(type){

  d3.selectAll("path")
       .remove();
  d3.selectAll("text")
        .remove();


  var parseDate = d3.time.format("%Y-%m-%d").parse;

  var x = d3.time.scale().range([0, width]);
  var y = d3.scale.linear().range([height, 0]);

  var xAxis = d3.svg.axis().scale(x)
    .orient("bottom").ticks(10);

  var yAxis = d3.svg.axis().scale(y)
    .orient("left").ticks(25);



  var line = d3.svg.line()
      .x(function(d) { return x(d.day); })
      .y(function(d) { if (type == "seats") return y(d.seats); if (type =="percent") return y(d.percent)});





  d3.tsv("/election2015/data/trends.csv", function(error, data) {


      data.forEach(function(d) {
          d.day = parseDate(d.day);
          if (type == "seats"){
            d.seats = +d.seats;
          }

          if (type == "percent"){
            d.percent = +d.percent;
          }


      });

      // Scale the range of the data
      x.domain(d3.extent(data, function(d) { return d.day; }));

      var yMinimum = d3.min(data, function(d) {

        if (type == "seats"){
          return d.seats ;
          }

        if (type == "percent"){
          return d.percent;

        }
      });

      var yMaximum = d3.max(data, function(d) {
        if (type == "seats")
          return d.seats + 40;
        if (type == "percent")
          return d.percent + 5; });

      y.domain([yMinimum, yMaximum]);


      var dataNest = d3.nest()
          .key(function(d) {return d.party;})
          .entries(data);


      dataNest.forEach(function(d) {

        if (graphParties.indexOf(d.key) >= 0){
          svg.append("path")

            .attr("class", d.key)
            .style("fill", "none")
            .attr("d", line(d.values))
            .on("mouseover", writeToTable(d.values))
            .on("mouseout", emptyTable)

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
        .attr("x", -height / 2 + 100)
        .attr("dy", ".75em")
        .attr("transform", "rotate(-90)")
        .text("Seats / Percentages");
  });

}


drawGraph(currentState);




function reDrawGraph(party){
  var indexOfParty = graphParties.indexOf(party)

  if (indexOfParty >= 0){
    graphParties.splice(indexOfParty, 1)
  }

  if (indexOfParty < 0){
    graphParties.push(party)
  }

  drawGraph(currentState);
}


function writeToTable(d) {
  return function(){
    console.log(d)
    $("#graphinfo").empty();
    $("#graphinfo").append("<h2>Date: " + "date" + "</h2>");
    $.each(graphParties, function(i){
      $("#graphinfo").append("<h3 class=\"" + graphParties[i] +"\">" + partylist[graphParties[i]] + ": number" + "</h3>")
    });
  }


}

function emptyTable(){

    $("#graphinfo").empty();
  
}
