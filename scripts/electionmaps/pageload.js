function getData(setting){

	$.when(
		$.getJSON(setting.mapurl, function(data){
			setting.polygons = data;
		}),
		$.getJSON(setting.dataurl, function(data){
			setting.seatData = data;
			currentMap = setting;
			//mapAttr.loadmap(currentMap);
		}),
		$.getJSON(setting.previous,function(data){
			setting.previousSeatData = data;
		})

	).then(function(){
		pageLoadEssentials();
	})
};

function pageLoadEssentials(){
	// function does all the random crap that needs changing / resetting on pageload

	// laod the map
	mapAttr.loadmap(currentMap);
	// reset filters.state - also get and show vote totals
	filters.opacities = {};
	filters.reset();
	params.checkParams();

	// reset user inputs
	$("#userinput-table input").each(function(){
		this.value = 0;
	});
	userInput.inputs = {};

	// populate seat search	// autocomplete function
	var seatList = Object.keys(currentMap.seatData);
	seatList.sort();

	$(function(){
		$("#searchseats").autocomplete({
			source: seatList,
			select: function(event, ui){
				searchSeats(ui.item.label);
			}
		});
	});

	// re-hide predict button on page change
	$("#predictbutton").addClass("hidden");

	//show and hide various divs on load
	uiAttr.pageLoadDiv();

	// if setting not currentParliament remove instructions
	if (currentMap.name != "election2017" && currentMap.name != "prediction"){
		$("#instructions").remove();
	} else {

		$(function(){
			$("#lastpollster").load("lastpollster.html");
		});
	}
	// hide instructions
	//setTimeout(function(){$("#instructions").remove();}, 20000);


	// election/prediction map differences to parliament maps
	if (currentMap.election == true){
		// change by election button text to gain
		$("#filter-byElection").text("Gains");
	} else if (currentMap.election == false){
		$("#filter-byElection").text("By-Elections");
	}
	if (currentMap.name == "election2010" || currentMap.name == "2017-600seat"){
		$("#filter-byElection").hide();
	} else {
		$("#filter-byElection").show();
	}

	// for reset redist
	currentMap.seatDataBackup = jQuery.extend(true, {}, currentMap.seatData);

	if (currentMap.predict == true){
		userInput.seatDataCopy = jQuery.extend(true, {}, currentMap.seatData);
	}

	if (currentMap.name == "2017-600seat" || currentMap.name == "prediction-600seat"){
		$("#seat-600 ").show();
	} else {
		$("#seat-600 ").hide();
	}

	// battlegrounds
	if (currentMap.battlegrounds == false){
		$("#battlegrounds").hide();
	} else {
		$("#battlegrounds").show();
	}

	if (currentMap.redistribute == false){
		$("#redistribute").hide();
	}

	// firefox css nonsense
	if(navigator.userAgent.toLowerCase().indexOf('firefox') > -1){
		$("div").css("font-size", "98%");
		$("select").css("font-size", "98%")
		// check this TO DO
	}

	if (currentMap.name == "myprediction"){
		$("#myprediction").show();
	} else {
		$("#myprediction").hide();
	}

	// if prediction show prediction button and div

};

// various page interaction functions
function searchSeats(value){
	mapAttr.clicked(currentMap.seatData[value].mapSelect);
};


function pageSetting(name, mapurl, dataurl, previous, election, predict, battlegrounds, redistribute){

	this.name = name;
	this.mapurl = mapurl;
	this.dataurl = dataurl;
	this.previous = previous;
	this.election = election;
	this.predict = predict;
	this.battlegrounds = battlegrounds;
	this.redistribute = redistribute;

	this.seatData = {};
	this.previousSeatData = {};
	this.polygons = {};
}

var currentMap;

var dataurls =  {
	// maps
	map650 : "650map.json",
	map600 : "600map.json",

	//parliaments
	predict : "houseofcommons/prediction.json",
	current : "houseofcommons/current.json",
	e2017 : "houseofcommons/2017election.json",
	e2015 : "houseofcommons/2015election.json",
	e2010 : "houseofcommons/2010election.json",
	e2017_600 : "houseofcommons/2017election_600seat.json",
	predict_600 : "houseofcommons/prediction_600seat.json"
}

var currentParliament = new pageSetting("current", dataurls.map650, dataurls.current, dataurls.e2017, false, false, false, false);
var election2017 = new pageSetting("election2017", dataurls.map650, dataurls.e2017, dataurls.e2015, true, false, true, false);
var election2015 = new pageSetting("election2015", dataurls.map650, dataurls.e2015, dataurls.e2010, true, false, false, false);
var election2010 = new pageSetting("election2010", dataurls.map650, dataurls.e2010, dataurls.e2010, false, false, false, false); // no 2005 data to compare atm
var prediction = new pageSetting("prediction", dataurls.map650, dataurls.predict, dataurls.e2017, true, false, true, false);
var predictit = new pageSetting("predictit", dataurls.map650, dataurls.e2017, dataurls.e2017, true, true, false, false);
var election2017_600seat = new pageSetting("2017-600seat", dataurls.map600, dataurls.e2017_600, dataurls.e2017_600, false, false, false, false); // nodata to compare
var prediction_600seat = new pageSetting("prediction-600seat", dataurls.map600, dataurls.predict_600, dataurls.e2017_600, true, false, false, false);


function initialization(){

	var name = getParameterByName("map", url);
	var setting = urlParamMap[name];

	uiAttr.changeNavBar(setting.name);

	$(".map").remove();

	$(document).ready(function(){
		getData(setting);
	});
}

function getParameterByName(name, url) {
    if (!url) {
      url = window.location.href;
    }
    name = name.replace(/[\[\]]/g, "\\$&");
    var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
        results = regex.exec(url);
    if (!results) return null;
    if (!results[2]) return '';
    return decodeURIComponent(results[2].replace(/\+/g, " "));
}

var url = window.location.href;

var urlParamMap = {
	null : election2017,
	"current" : currentParliament,
	"prediction" : prediction,
	"predictit" : predictit,
	"election2017" : election2017,
	"election2015" : election2015,
	"election2010" : election2010,
	"election2017_600seat" : election2017_600seat,
	"prediction_600seat" : prediction_600seat
};

// var countdown = {
//
// 	run : function(){
// 		var end = new Date(2017, 5, 8, 22, 0, 0);
//
//   	var timeinterval = setInterval(function(){
// 	    var t = countdown.getDiff(end);
// 			var toAdd = t.hours + " hrs " + t.minutes + " mins " + t.seconds + " secs ";
// 			$("#daysto").text(toAdd);
// 		  if(t.total<=0){
// 					$("#daysto").text("Polls closed")
// 		      clearInterval(timeinterval);
// 		    }
// 		  },1000);
// 		},
//
// 	getDiff: function(endTime){
// 		var t = Date.parse(endTime) - Date.parse(new Date());
// 		var seconds = Math.floor( (t/1000) % 60 );
// 		var minutes = Math.floor( (t/1000/60) % 60 );
// 		var hours = Math.floor( (t/(1000*60*60)) % 24 );
// 		var days = Math.floor( t/(1000*60*60*24) );
// 		return {
// 		 'total': t,
// 		 'days': days,
// 		 'hours': hours,
// 		 'minutes': minutes,
// 		 'seconds': seconds
// 		};
// 	}
//
// };
